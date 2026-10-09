"""F1 exact scalar recipes, closed npm build profiles and measured page risk."""

from dataclasses import replace
from uuid import UUID

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Parser

from signal_core.astro_recipes import (
    MAX_IMPACT_PAGES,
    _walk,
    astro_recipe_release_manifest,
    verify_astro_impact,
)
from signal_core.astro_source import CONFIGS, LOCKS, inspect_astro_source, strict_json
from signal_core.candidate_build import _protected, _valid_path
from signal_core.front_matter import (
    FrontMatterUnavailable,
    parse_front_matter,
    prove_front_matter_edit,
)
from signal_core.technical_seo_recipes import RecipePatch, TechnicalRecipeUnavailable

FRONT_MATTER_RECIPES = {
    "front_matter_title": "technical_title",
    "front_matter_description": "technical_description",
}
NON_NPM_REASON = "build verification unavailable: no pinned offline toolchain"


def front_matter_recipe_release_manifest(key: str, release_id: UUID, *, version="1.0.0"):
    if key not in FRONT_MATTER_RECIPES:
        raise ValueError("Unsupported front-matter recipe.")
    manifest = astro_recipe_release_manifest(
        key.replace("front_matter_", "astro_"), release_id, version=version
    )
    return dict(
        manifest,
        recipe_key=key,
        allowed_resource_types=["front_matter_content"],
        purpose="Resolve one evidence-bound front-matter finding within an exact built scope.",
    )


def classify_front_matter_scope(files: dict[str, bytes], path: str, impact_count: int) -> str:
    parts = {part.casefold() for part in path.split("/")}
    if (
        not _valid_path(path)
        or _protected(path)
        or path not in files
        or not path.endswith((".md", ".mdx", ".njk"))
        or parts
        & {
            "layouts",
            "layout",
            "components",
            "config",
            "_layouts",
            "_includes",
            "includes",
            "data",
            "_data",
        }
        or "config" in path.rsplit("/", 1)[-1].casefold()
    ):
        raise TechnicalRecipeUnavailable("FRONT_MATTER_PROTECTED_PATH")
    if type(impact_count) is not int or not 1 <= impact_count <= MAX_IMPACT_PAGES:
        raise TechnicalRecipeUnavailable("FRONT_MATTER_IMPACT_UNAVAILABLE")
    if CONFIGS & files.keys() and path not in inspect_astro_source(files).collection_entries:
        raise TechnicalRecipeUnavailable("FRONT_MATTER_COLLECTION_UNAVAILABLE")
    try:
        parse_front_matter(files[path])
    except FrontMatterUnavailable:
        raise TechnicalRecipeUnavailable("FRONT_MATTER_PARSE_UNAVAILABLE") from None
    return "A2" if impact_count == 1 and "[" not in path and "]" not in path else "A4"


def make_front_matter_recipe_patch(*, files, recipe_key, path, offset, before, after):
    if recipe_key not in FRONT_MATTER_RECIPES:
        raise TechnicalRecipeUnavailable("FRONT_MATTER_RECIPE_UNAVAILABLE")
    classify_front_matter_scope(files, path, 1)
    try:
        result = prove_front_matter_edit(
            files[path],
            field=recipe_key.removeprefix("front_matter_"),
            offset=offset,
            before=before,
            after=after,
        )
    except FrontMatterUnavailable:
        raise TechnicalRecipeUnavailable("FRONT_MATTER_PARSE_UNAVAILABLE") from None
    return RecipePatch(
        path,
        files[path],
        result,
        before,
        after,
        offset,
        "Exact declared built pages only; no production publication.",
        "Revert only this exact source fragment in a separate owner-reviewed PR; "
        "conflict on later edits.",
    )


def front_matter_build_profile(files, framework):
    if framework in {"hugo", "jekyll"}:
        raise TechnicalRecipeUnavailable(NON_NPM_REASON)
    if framework == "astro":
        source = inspect_astro_source(files)
        return source.output_directory, source.lockfile_path, "astro build"
    if framework not in {"eleventy", "nextjs"}:
        raise TechnicalRecipeUnavailable("FRONT_MATTER_FORMAT_UNAVAILABLE")
    package = strict_json(files.get("package.json", b""))
    locks = LOCKS & files.keys()
    dependency = "@11ty/eleventy" if framework == "eleventy" else "next"
    if len(locks) != 1 or not any(
        isinstance(package.get(key), dict) and isinstance(package[key].get(dependency), str)
        for key in ("dependencies", "devDependencies")
    ):
        raise TechnicalRecipeUnavailable("FRONT_MATTER_LOCKFILE_UNAVAILABLE")
    if framework == "nextjs":
        configs = {"next.config.js", "next.config.mjs", "next.config.ts"} & files.keys()
        if len(configs) != 1:
            raise TechnicalRecipeUnavailable("NEXT_STATIC_EXPORT_UNAVAILABLE")
        path = next(iter(configs))
        grammar = (
            tree_sitter_typescript.language_typescript()
            if path.endswith(".ts")
            else tree_sitter_javascript.language()
        )
        tree = Parser(Language(grammar)).parse(files[path])
        objects = []
        for node in _walk(tree.root_node):
            if node.type == "export_statement" and any(c.type == "default" for c in node.children):
                objects.append(node.child_by_field_name("value"))
            elif (
                node.type == "assignment_expression"
                and node.child_by_field_name("left").text == b"module.exports"
            ):
                objects.append(node.child_by_field_name("right"))
        if tree.root_node.has_error or len(objects) != 1 or objects[0].type != "object":
            raise TechnicalRecipeUnavailable("NEXT_STATIC_EXPORT_UNAVAILABLE")
        outputs = []
        for pair in objects[0].named_children:
            if pair.type == "comment":
                continue
            if pair.type != "pair":
                raise TechnicalRecipeUnavailable("NEXT_STATIC_EXPORT_UNAVAILABLE")
            key = pair.child_by_field_name("key")
            if key.text.strip(b"\"'") == b"output":
                outputs.append(pair.child_by_field_name("value").text)
        if outputs not in ([b'"export"'], [b"'export'"]):
            raise TechnicalRecipeUnavailable("NEXT_STATIC_EXPORT_UNAVAILABLE")
    return (
        ("_site", next(iter(locks)), "eleventy")
        if framework == "eleventy"
        else ("out", next(iter(locks)), "next build")
    )


def verify_front_matter_impact(**kwargs):
    key = kwargs.pop("recipe_key")
    if key not in FRONT_MATTER_RECIPES:
        raise TechnicalRecipeUnavailable("FRONT_MATTER_RECIPE_UNAVAILABLE")
    impact = verify_astro_impact(
        **kwargs,
        recipe_key=key.replace("front_matter_", "astro_"),
        scope_classifier=classify_front_matter_scope,
    )
    return replace(
        impact,
        approval_class=classify_front_matter_scope(
            kwargs["files"], kwargs["path"], len(impact.pages)
        ),
    )
