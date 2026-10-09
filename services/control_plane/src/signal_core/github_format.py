"""Deterministic framework and content-format inference from Git tree metadata."""

from dataclasses import dataclass

from signal_core.astro_source import (
    CONFIGS,
    LOCKS,
    AstroSourceUnavailable,
    astro_metadata_paths,
    inspect_astro_source,
)
from signal_core.front_matter import FrontMatterUnavailable, parse_front_matter
from signal_core.github_app import GitHubRepositoryInventory, GitHubTreeEntry

_NEXT_MARKERS = {"next.config.js", "next.config.mjs", "next.config.ts"}
_ASTRO_MARKERS = CONFIGS
_HUGO_MARKERS = {"hugo.toml", "hugo.yaml", "hugo.yml", "hugo.json"}
_JEKYLL_MARKERS = {"_config.yml", "_config.yaml"}
_ELEVENTY_MARKERS = {
    ".eleventy.js",
    ".eleventy.cjs",
    "eleventy.config.js",
    "eleventy.config.cjs",
    "eleventy.config.mjs",
}
_FORMAT_BY_SUFFIX = {
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".md": "markdown",
    ".mdx": "mdx",
    ".astro": "astro",
    ".html": "html",
    ".liquid": "liquid",
    ".njk": "nunjucks",
}


@dataclass(frozen=True)
class RepositoryFormatAssessment:
    framework: str
    content_format: str
    coverage: str
    marker_entries: tuple[GitHubTreeEntry, ...]
    content_entry: GitHubTreeEntry | None
    base_sha: str
    tree_sha: str
    output_directory: str | None = None
    collection_entries: tuple[str, ...] = ()
    build_unavailable_reason: str | None = None
    front_matter_entries: tuple[str, ...] = ()
    nextjs_routers: tuple[str, ...] = ()


def detect_repository_format(
    inventory: GitHubRepositoryInventory, files: dict[str, bytes] | None = None
) -> RepositoryFormatAssessment:
    """Classify only observed root markers and the owner's exact content file."""
    if not isinstance(inventory, GitHubRepositoryInventory):
        raise ValueError("A validated GitHub inventory is required.")
    blobs = {
        entry.path: entry
        for entry in inventory.entries
        if entry.kind == "blob" and entry.mode == "100644"
    }
    content = blobs.get(inventory.snapshot.content_path)
    markers_by_framework = {
        "nextjs": _NEXT_MARKERS,
        "astro": _ASTRO_MARKERS,
        "hugo": _HUGO_MARKERS,
        "eleventy": _ELEVENTY_MARKERS,
        "jekyll": _JEKYLL_MARKERS,
    }
    found = {
        framework: tuple(blobs[path] for path in sorted(paths & blobs.keys()))
        for framework, paths in markers_by_framework.items()
        if paths & blobs.keys()
    }
    markers = tuple(entry for entries in found.values() for entry in entries)
    if inventory.truncated:
        framework = "unknown"
    elif len(found) == 1:
        framework = next(iter(found))
    elif len(found) > 1:
        framework = "ambiguous"
    else:
        framework = "unknown"
    content_format = "unknown"
    if content is not None:
        for suffix, value in _FORMAT_BY_SUFFIX.items():
            if content.path.lower().endswith(suffix):
                content_format = value
                break
    coverage = "complete" if not inventory.truncated and content is not None else "partial"
    astro = None
    reason = None
    front_matter = []
    routers = ()
    next_output = None
    if framework == "nextjs":
        routers = tuple(
            router
            for router, roots in (
                ("app", ("app/", "src/app/")),
                ("pages", ("pages/", "src/pages/")),
            )
            if any(path.startswith(roots) and path.endswith((".tsx", ".jsx")) for path in blobs)
        )
        if files is None:
            reason = "NEXT_STATIC_EXPORT_UNOBSERVED"
        else:
            from signal_core.nextjs_recipes import nextjs_build_profile
            from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

            try:
                next_output = nextjs_build_profile(files)[0]
            except TechnicalRecipeUnavailable as error:
                reason = str(error)
    if files is not None and framework in {"eleventy", "astro", "nextjs", "hugo", "jekyll"}:
        for path in sorted(blobs.keys() & files.keys()):
            if path.endswith((".md", ".mdx", ".njk")):
                try:
                    parse_front_matter(files[path])
                except FrontMatterUnavailable:
                    continue
                front_matter.append(path)
    if framework in {"hugo", "jekyll"}:
        reason = "build verification unavailable: no pinned offline toolchain"
    if framework == "astro":
        if files is None:
            framework = "unknown"
            reason = "ASTRO_DEPENDENCY_UNOBSERVED"
        else:
            try:
                astro_metadata_paths(files)
            except AstroSourceUnavailable as error:
                framework = "unknown"
                reason = str(error)
            else:
                try:
                    astro = inspect_astro_source(files)
                except AstroSourceUnavailable as error:
                    reason = str(error)
        markers += tuple(blobs[path] for path in sorted(({"package.json"} | LOCKS) & blobs.keys()))
    return RepositoryFormatAssessment(
        framework=framework,
        content_format=content_format,
        coverage=coverage,
        marker_entries=markers,
        content_entry=content,
        base_sha=inventory.snapshot.base_sha,
        tree_sha=inventory.tree_sha,
        output_directory=astro.output_directory if astro else next_output,
        collection_entries=astro.collection_entries if astro else (),
        build_unavailable_reason=reason,
        front_matter_entries=tuple(
            p
            for p in front_matter
            if framework != "astro" or astro and p in astro.collection_entries
        ),
        nextjs_routers=routers,
    )
