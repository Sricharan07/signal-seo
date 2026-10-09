"""Deterministic candidate checkout, recipe scope, and build-plan policy."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from signal_core.astro_source import AstroSourceUnavailable, inspect_astro_source
from signal_core.github_app import GitHubRepositoryCheckout
from signal_core.github_pr_extension import GitHubPrExtension
from signal_core.npm_registry import LockedDependencies, NpmRegistryUnavailable, locked_dependencies

NODE_IMAGE = (
    "node:22.18.0-bookworm-slim@"
    "sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e"
)
EMPTY_PATCH_SHA256 = hashlib.sha256(b"").hexdigest()
_ARTIFACT_ROOT = {"nextjs": ".next", "astro": "dist", "eleventy": "_site"}
_PROTECTED_PARTS = {
    ".git",
    ".github",
    ".env",
    "secrets",
    "secret",
    "auth",
    "authentication",
    "payments",
    "payment",
    "infra",
    "infrastructure",
    "deploy",
    "deployment",
}
_PROTECTED_NAMES = {
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    "dockerfile",
    "compose.yaml",
    "docker-compose.yml",
}


class CandidatePolicyRejected(Exception):
    """Untrusted checkout, patch, or build profile violates one closed rule."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CandidateBuildPlan:
    base_sha: str
    tree_sha: str
    patch_sha256: str
    toolchain: str
    command: tuple[str, ...]
    artifact_root: str
    files: tuple[tuple[str, bytes], ...]
    dependency_manifest: LockedDependencies | None = None
    dependencies: tuple[tuple[str, bytes], ...] = ()


def plan_candidate_build(
    extension: GitHubPrExtension,
    checkout: GitHubRepositoryCheckout,
    *,
    patch: Mapping[str, bytes] | None = None,
    approved_paths: frozenset[str] = frozenset(),
    astro_recipe=None,
    front_matter_recipe=None,
    nextjs_recipe=None,
    indexnow_key=None,
) -> CandidateBuildPlan:
    """Reject a mismatched or unsafe source before any sandbox is started."""
    if not isinstance(extension, GitHubPrExtension) or not extension.candidate_compatible:
        raise CandidatePolicyRejected("CANDIDATE_EXTENSION_INACTIVE")
    if not isinstance(checkout, GitHubRepositoryCheckout):
        raise CandidatePolicyRejected("CANDIDATE_CHECKOUT_INVALID")
    inventory = checkout.inventory
    snapshot = inventory.snapshot
    if (
        inventory.truncated
        or snapshot.repository_id != extension.repository_id
        or snapshot.base_sha != extension.base_sha
        or inventory.tree_sha != extension.tree_sha
        or not (
            snapshot.protected
            or (
                extension.owner_accepted_unprotected
                and snapshot.default_branch
                == snapshot.base_branch
                == extension.accepted_default_branch
            )
        )
    ):
        raise CandidatePolicyRejected("CANDIDATE_SOURCE_CHANGED")
    if extension.framework not in _ARTIFACT_ROOT:
        raise CandidatePolicyRejected("CANDIDATE_TOOLCHAIN_UNAVAILABLE")
    entries = {entry.path: entry for entry in inventory.entries if entry.kind == "blob"}
    source = dict(checkout.files)
    if (
        len(source) != len(checkout.files)
        or set(source) != set(entries)
        or len(source) > 256
        or sum(len(content) for content in source.values()) > 8 * 1024 * 1024
    ):
        raise CandidatePolicyRejected("CANDIDATE_CHECKOUT_INVALID")
    for path, content in source.items():
        entry = entries[path]
        if (
            not _valid_path(path)
            or entry.mode != "100644"
            or not isinstance(content, bytes)
            or len(content) > 128 * 1024
            or _git_blob_sha(content) != entry.sha
            or content.startswith(b"version https://git-lfs.github.com/spec/v1")
        ):
            raise CandidatePolicyRejected("CANDIDATE_CHECKOUT_INVALID")
    if any(str(parent) in source for path in source for parent in PurePosixPath(path).parents):
        raise CandidatePolicyRejected("CANDIDATE_CHECKOUT_INVALID")
    target = entries.get(snapshot.content_path)
    if target is None or target.sha != extension.content_sha:
        raise CandidatePolicyRejected("CANDIDATE_SOURCE_CHANGED")
    changes = patch or {}
    front_matter = front_matter_recipe is not None
    nextjs = nextjs_recipe is not None
    if (
        sum(
            value is not None
            for value in (front_matter_recipe, nextjs_recipe, astro_recipe, indexnow_key)
        )
        > 1
    ):
        raise CandidatePolicyRejected("CANDIDATE_PATCH_REJECTED")
    if nextjs and extension.framework != "nextjs":
        raise CandidatePolicyRejected("NEXT_DELIVERY_UNAVAILABLE")
    if nextjs and changes:
        from signal_core.nextjs_recipes import make_nextjs_recipe_patch
        from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

        try:
            recipe = make_nextjs_recipe_patch(files=source, **nextjs_recipe)
            if changes != recipe.patch or approved_paths != frozenset({recipe.path}):
                raise ValueError
        except (TechnicalRecipeUnavailable, TypeError, ValueError):
            raise CandidatePolicyRejected("NEXT_DELIVERY_UNAVAILABLE") from None
    elif front_matter and changes:
        from signal_core.front_matter_recipes import make_front_matter_recipe_patch
        from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

        try:
            recipe = make_front_matter_recipe_patch(files=source, **front_matter_recipe)
            if changes != recipe.patch or approved_paths != frozenset({recipe.path}):
                raise ValueError
        except (TechnicalRecipeUnavailable, TypeError, ValueError):
            raise CandidatePolicyRejected("FRONT_MATTER_DELIVERY_UNAVAILABLE") from None
    elif indexnow_key is not None and changes:
        from signal_core.indexnow_placement import indexnow_key_placement

        try:
            path, _ = indexnow_key_placement(extension.framework, source, indexnow_key)
            if (
                path in source
                or changes != {path: indexnow_key.encode()}
                or approved_paths != frozenset({path})
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise CandidatePolicyRejected("INDEXNOW_KEY_PLACEMENT_REJECTED") from None
    elif extension.framework == "astro" and changes:
        from signal_core.astro_recipes import make_astro_recipe_patch
        from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

        try:
            if not isinstance(astro_recipe, dict):
                raise ValueError
            recipe = make_astro_recipe_patch(files=source, **astro_recipe)
            if changes != recipe.patch or approved_paths != frozenset({recipe.path}):
                raise ValueError
        except (TechnicalRecipeUnavailable, TypeError, ValueError):
            raise CandidatePolicyRejected("ASTRO_DELIVERY_UNAVAILABLE") from None
    if not isinstance(changes, Mapping) or len(changes) > 32:
        raise CandidatePolicyRejected("CANDIDATE_PATCH_REJECTED")
    if any(not _valid_path(path) for path in approved_paths):
        raise CandidatePolicyRejected("CANDIDATE_PATCH_REJECTED")
    for path, content in changes.items():
        if (
            not _valid_path(path)
            or path not in approved_paths
            or _protected(path)
            or not isinstance(content, bytes)
            or not 0 < len(content) <= 128 * 1024
            or b"\x00" in content
            or (b"\r" in content and not front_matter)
        ):
            raise CandidatePolicyRejected("CANDIDATE_PATCH_REJECTED")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise CandidatePolicyRejected("CANDIDATE_PATCH_REJECTED") from None
        if path in source and b"\r" in source[path] and not front_matter:
            raise CandidatePolicyRejected("CANDIDATE_PATCH_REJECTED")
        if any(
            str(parent) in source or str(parent) in changes
            for parent in PurePosixPath(path).parents
        ):
            raise CandidatePolicyRejected("CANDIDATE_PATCH_REJECTED")
    source.update(changes)
    package = source.get("package.json")
    if not isinstance(package, bytes) or len(package) > 64 * 1024:
        raise CandidatePolicyRejected("CANDIDATE_BUILD_COMMAND_UNAVAILABLE")
    try:
        document = json.loads(package)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CandidatePolicyRejected("CANDIDATE_BUILD_COMMAND_UNAVAILABLE") from None
    scripts = document.get("scripts") if isinstance(document, dict) else None
    build = scripts.get("build") if isinstance(scripts, dict) else None
    if (
        not isinstance(build, str)
        or not 1 <= len(build) <= 256
        or any(ord(character) < 32 or ord(character) == 127 for character in build)
        or (
            extension.framework != "astro"
            and not front_matter
            and not nextjs
            and ("prebuild" in scripts or "postbuild" in scripts)
        )
    ):
        raise CandidatePolicyRejected("CANDIDATE_BUILD_COMMAND_UNAVAILABLE")
    dependency_manifest = None
    artifact_root = _ARTIFACT_ROOT[extension.framework]
    if front_matter or nextjs or extension.framework == "astro":
        try:
            from signal_core.front_matter_recipes import front_matter_build_profile
            from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

            if nextjs:
                from signal_core.nextjs_recipes import nextjs_build_profile

                artifact_root, lockfile, expected_build = nextjs_build_profile(source)
            elif front_matter:
                artifact_root, lockfile, expected_build = front_matter_build_profile(
                    source, extension.framework
                )
            else:
                astro = inspect_astro_source(source)
                artifact_root, lockfile, expected_build = (
                    astro.output_directory,
                    astro.lockfile_path,
                    "astro build",
                )
            dependency_manifest = locked_dependencies(package, source[lockfile])
        except (
            AstroSourceUnavailable,
            NpmRegistryUnavailable,
            TechnicalRecipeUnavailable,
        ) as error:
            raise CandidatePolicyRejected(str(error)) from None
        if build != expected_build or any(
            path.split("/")[0]
            in {"node_modules", ".astro", ".signal-dependencies", ".signal-npm-cache"}
            or path.endswith((".npmrc", ".yarnrc"))
            or any(part.startswith(".env") for part in path.split("/"))
            for path in source
        ):
            raise CandidatePolicyRejected("ASTRO_BUILD_PROFILE_UNAVAILABLE")
    if changes:
        manifest = [(path, hashlib.sha256(changes[path]).hexdigest()) for path in sorted(changes)]
        patch_sha256 = hashlib.sha256(
            json.dumps(manifest, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        ).hexdigest()
    else:
        patch_sha256 = EMPTY_PATCH_SHA256
    return CandidateBuildPlan(
        base_sha=extension.base_sha,
        tree_sha=inventory.tree_sha,
        patch_sha256=patch_sha256,
        toolchain=NODE_IMAGE,
        command=("npm", "run", "build"),
        artifact_root=artifact_root,
        files=tuple(sorted(source.items())),
        dependency_manifest=dependency_manifest,
    )


def _valid_path(path: object) -> bool:
    if not isinstance(path, str) or not path.isascii() or not 1 <= len(path) <= 1024:
        return False
    if path.startswith("/") or "\\" in path or any(ord(char) < 32 for char in path):
        return False
    return all(part not in {"", ".", ".."} for part in path.split("/"))


def _protected(path: str) -> bool:
    parts = [part.casefold() for part in path.split("/")]
    name = parts[-1]
    return (
        any(part in _PROTECTED_PARTS or part.startswith(".env") for part in parts)
        or name in _PROTECTED_NAMES
        or name.endswith((".lock", ".pem", ".key", ".p12", ".pfx"))
        or "secret" in name
        or path.startswith(".")
    )


def _git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(f"blob {len(content)}\0".encode("ascii") + content).hexdigest()
