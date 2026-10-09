"""Reconstruct one sealed technical patch and its exact Git object identities."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import UUID

import rfc8785

from signal_core.candidate_build import CandidatePolicyRejected, plan_candidate_build
from signal_core.github_app import GitHubRepositoryCheckout
from signal_core.github_pr_extension import GitHubPrExtension
from signal_core.indexnow_protocol import valid_indexnow_key


class GitHubPrPatchRejected(Exception):
    """The sealed patch does not equal the current, allowed candidate source."""


@dataclass(frozen=True)
class GitHubPrPatch:
    path: str
    content: bytes
    patch_sha256: str
    tree_sha: str
    commit_sha: str
    commit_message: str
    commit_date: str


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(f"blob {len(content)}\0".encode("ascii") + content).hexdigest()


def git_tree_sha(files: dict[str, bytes]) -> str:
    """Hash the complete regular-file Git tree, including nested directories."""
    root: dict[str, object] = {}
    for path, content in files.items():
        if not isinstance(path, str) or not isinstance(content, bytes):
            raise GitHubPrPatchRejected("PR_TREE_INVALID")
        node = root
        parts = path.split("/")
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise GitHubPrPatchRejected("PR_TREE_INVALID")
            node = child
        if parts[-1] in node:
            raise GitHubPrPatchRejected("PR_TREE_INVALID")
        node[parts[-1]] = content

    def digest(node: dict[str, object]) -> str:
        entries = bytearray()
        for name, child in sorted(
            node.items(), key=lambda item: item[0] + ("/" if isinstance(item[1], dict) else "")
        ):
            mode = b"40000" if isinstance(child, dict) else b"100644"
            sha = digest(child) if isinstance(child, dict) else git_blob_sha(child)
            entries.extend(mode + b" " + name.encode("utf-8") + b"\0" + bytes.fromhex(sha))
        return hashlib.sha1(f"tree {len(entries)}\0".encode("ascii") + entries).hexdigest()

    return digest(root)


def git_commit_sha(*, tree_sha: str, base_sha: str, message: str, created_at: datetime) -> str:
    timestamp = int(created_at.astimezone(UTC).timestamp())
    identity = f"Signal <signal@users.noreply.github.com> {timestamp} +0000"
    body = (
        f"tree {tree_sha}\nparent {base_sha}\nauthor {identity}\ncommitter {identity}\n\n{message}"
    ).encode()
    return hashlib.sha1(f"commit {len(body)}\0".encode("ascii") + body).hexdigest()


def plan_github_pr_patch(
    *,
    manifest_bytes: bytes,
    revision_sha256: str,
    operation_id: UUID,
    created_at: datetime,
    extension: GitHubPrExtension,
    checkout: GitHubRepositoryCheckout,
) -> GitHubPrPatch:
    try:
        manifest = json.loads(manifest_bytes)
        if rfc8785.dumps(manifest) != manifest_bytes:
            raise ValueError
        if hashlib.sha256(manifest_bytes).hexdigest() != revision_sha256:
            raise ValueError
        article = manifest.get("work_type") in {"new_article", "content_refresh"}
        front_matter = manifest.get("content_adapter") == "front_matter"
        nextjs = manifest.get("content_adapter") == "nextjs_metadata"
        astro = manifest.get("framework") == "astro" and not front_matter and not nextjs
        indexnow = (
            manifest.get("evidence", {}).get("finding", {}).get("key") == "indexnow.key.required"
        )
        astro_key = indexnow and extension.framework == "astro"
        if manifest.get("schema_version") != 1 or manifest.get("approval_class") not in (
            {"A4"}
            if astro_key
            else {"A2"}
            if article
            else {"A2", "A4"}
            if astro or front_matter or nextjs
            else {"owner_review"}
        ):
            raise ValueError
        if manifest.get("base_sha") != extension.base_sha or manifest.get("extension_id") != str(
            extension.id
        ):
            raise ValueError
        if not article and (
            not isinstance(manifest.get("evidence"), dict)
            or not manifest["evidence"].get("finding")
        ):
            raise ValueError
        if not isinstance(manifest.get("recovery_plan"), str) or not manifest["recovery_plan"]:
            raise ValueError
        receipt = manifest.get("build_receipt")
        if (
            not isinstance(receipt, dict)
            or receipt.get("exit_class") != "passed"
            or not receipt.get("artifacts")
        ):
            raise ValueError
        if article:
            if (
                manifest.get("autonomy_eligible") is not False
                or len(manifest["changed_files"]) != 1
            ):
                raise ValueError
            change = manifest["changed_files"][0]
            path = change["path"]
            source = dict(checkout.files).get(path, b"")
            if (
                PurePosixPath(path).parent
                != PurePosixPath(checkout.inventory.snapshot.content_path).parent
                or not path.endswith(".html")
                or (manifest["work_type"] == "new_article" and path in dict(checkout.files))
                or (
                    manifest["work_type"] == "content_refresh"
                    and path != checkout.inventory.snapshot.content_path
                )
                or source != change["before"].encode("utf-8")
                or hashlib.sha256(source).hexdigest() != change["source_sha256"]
                or manifest["originality"]["state"] != "original"
                or manifest["grounding"]["state"] not in {"grounded", "owner_required"}
            ):
                raise ValueError
            result = change["after"].encode("utf-8")
            result_sha256 = change["result_sha256"]
        else:
            path = manifest["source_path"]
            addition = manifest["evidence"]["finding"].get("key") == "indexnow.key.required"
            if addition:
                from signal_core.indexnow_placement import indexnow_key_placement

                expected_path, expected_output = indexnow_key_placement(
                    extension.framework, dict(checkout.files), manifest["patch"]["after"]
                )
                if astro_key and (
                    manifest.get("autonomy_eligible") is not False
                    or manifest.get("static_key_placement", {}).get("output_path")
                    != expected_output
                ):
                    raise ValueError
            if addition and (
                path in dict(checkout.files)
                or manifest.get("audit_report_id") is not None
                or manifest["patch"]["offset"] != 0
                or manifest["patch"]["before"] != ""
                or not valid_indexnow_key(manifest["patch"]["after"])
                or path != expected_path
            ):
                raise ValueError
            source = b"" if addition else dict(checkout.files)[path]
            if hashlib.sha256(source).hexdigest() != manifest["source_sha256"]:
                raise ValueError
            patch = manifest["patch"]
            offset, before, after = patch["offset"], patch["before"], patch["after"]
            if (
                type(offset) is not int
                or offset < 0
                or not isinstance(before, str)
                or not isinstance(after, str)
            ):
                raise ValueError
            if astro or front_matter or nextjs:
                # Built adapters seal character offsets; static recipes seal byte offsets.
                text = source.decode("utf-8")
                if text[offset : offset + len(before)] != before:
                    raise ValueError
                result = (text[:offset] + after + text[offset + len(before) :]).encode("utf-8")
            else:
                before_bytes, after_bytes = before.encode("utf-8"), after.encode("utf-8")
                if source[offset : offset + len(before_bytes)] != before_bytes:
                    raise ValueError
                result = source[:offset] + after_bytes + source[offset + len(before_bytes) :]
            result_sha256 = manifest["result_sha256"]
        if hashlib.sha256(result).hexdigest() != result_sha256:
            raise ValueError
        if manifest.get("internal_link") is not None:
            from signal_core.internal_linking import make_internal_link_patch
            from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

            link = manifest["internal_link"]
            if (
                link.get("autonomy_eligible") is not False
                or link.get("recipe_key") != "technical_internal_link_add"
            ):
                raise ValueError
            try:
                checked = make_internal_link_patch(
                    evidence={
                        **manifest["evidence"],
                        "page_body_sha256": manifest["source_sha256"],
                    },
                    extension=extension,
                    checkout=checkout,
                    opportunity=link["graph"],
                    source_path=path,
                    selected_anchor=manifest["patch"]["before"],
                )
            except TechnicalRecipeUnavailable:
                raise ValueError from None
            if checked.after != result or checked.offset != manifest["patch"]["offset"]:
                raise ValueError
        astro_recipe = None
        front_matter_recipe = None
        nextjs_recipe = None
        if nextjs:
            from signal_core.nextjs_recipes import classify_nextjs_scope
            from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

            try:
                if (
                    extension.framework != "nextjs"
                    or manifest.get("framework") != "nextjs"
                    or manifest.get("autonomy_eligible") is not False
                ):
                    raise ValueError
                if (
                    classify_nextjs_scope(
                        dict(checkout.files), path, manifest["built_impact"]["page_count"]
                    )
                    != manifest["approval_class"]
                ):
                    raise ValueError
                nextjs_recipe = dict(
                    recipe_key=manifest["recipe_key"],
                    path=path,
                    offset=offset,
                    before=before,
                    after=after,
                )
            except TechnicalRecipeUnavailable:
                raise ValueError from None
        elif front_matter:
            from signal_core.front_matter_recipes import classify_front_matter_scope
            from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

            try:
                if (
                    manifest.get("framework") != extension.framework
                    or manifest.get("autonomy_eligible") is not False
                ):
                    raise ValueError
                if (
                    classify_front_matter_scope(
                        dict(checkout.files), path, manifest["built_impact"]["page_count"]
                    )
                    != manifest["approval_class"]
                ):
                    raise ValueError
                front_matter_recipe = dict(
                    recipe_key=manifest["recipe_key"],
                    path=path,
                    offset=offset,
                    before=before,
                    after=after,
                )
            except TechnicalRecipeUnavailable:
                raise ValueError from None
        elif astro:
            from signal_core.astro_recipes import classify_astro_scope
            from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

            try:
                if extension.framework != "astro" or manifest.get("autonomy_eligible") is not False:
                    raise ValueError
                if (
                    classify_astro_scope(
                        dict(checkout.files), path, manifest["built_impact"]["page_count"]
                    )
                    != manifest["approval_class"]
                ):
                    raise ValueError
            except TechnicalRecipeUnavailable:
                raise ValueError from None
            astro_recipe = dict(
                recipe_key=manifest["recipe_key"],
                path=path,
                offset=offset,
                before=before,
                after=after,
            )
        plan = plan_candidate_build(
            extension,
            checkout,
            patch={path: result},
            approved_paths=frozenset({path}),
            astro_recipe=astro_recipe,
            front_matter_recipe=front_matter_recipe,
            nextjs_recipe=nextjs_recipe,
            indexnow_key=manifest["patch"]["after"] if indexnow else None,
        )
        if plan.patch_sha256 != manifest["patch_sha256"]:
            raise ValueError
        if git_tree_sha(dict(checkout.files)) != checkout.inventory.tree_sha:
            raise ValueError
        expected_tree = git_tree_sha(dict(plan.files))
        message = (
            f"Deliver content candidate {manifest['draft_id']}\n\n"
            if article
            else f"Fix technical SEO finding {manifest['finding_id']}\n\n"
        ) + f"Signal-Revision: {revision_sha256}\nSignal-Operation: {operation_id}\n"
        date = created_at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        commit = git_commit_sha(
            tree_sha=expected_tree,
            base_sha=plan.base_sha,
            message=message,
            created_at=created_at,
        )
        return GitHubPrPatch(path, result, plan.patch_sha256, expected_tree, commit, message, date)
    except (
        CandidatePolicyRejected,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        IndexError,
    ) as error:
        raise GitHubPrPatchRejected("PR_PATCH_DRIFT_OR_SCOPE") from error
