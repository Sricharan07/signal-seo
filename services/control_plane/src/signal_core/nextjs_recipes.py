"""F2: exact Next.js literal spans and conservative measured static-export scope."""

from dataclasses import dataclass
from pathlib import PurePosixPath
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
from signal_core.candidate_build import _protected, _valid_path
from signal_core.front_matter_recipes import front_matter_build_profile
from signal_core.technical_seo_recipes import RecipePatch, TechnicalRecipeUnavailable

NEXTJS_RECIPES = {"nextjs_title": "technical_title", "nextjs_description": "technical_description"}
NEXT_EXPORT_REASON = "build verification unavailable: Next.js requires a literal static export"
NEXT_NETWORK_REASON = (
    "build verification unavailable: Next.js requires network access; network disabled"
)
ROOTS = ("app/", "src/app/", "pages/", "src/pages/")


@dataclass(frozen=True)
class _LiteralText:
    start_byte: int
    end_byte: int
    text: bytes
    type: str = "jsx_text"


def nextjs_build_profile(files):
    try:
        profile = front_matter_build_profile(files, "nextjs")
    except (ValueError, TechnicalRecipeUnavailable):
        raise TechnicalRecipeUnavailable(NEXT_EXPORT_REASON) from None
    for path, raw in files.items():
        if path.endswith((".tsx", ".jsx", ".ts", ".js", ".mjs")):
            tree = _tree(raw, path)
            if any(
                node.type == "import_statement"
                and _import_source(node) in {b'"next/font/google"', b"'next/font/google'"}
                for node in tree.root_node.named_children
            ):
                raise TechnicalRecipeUnavailable(NEXT_NETWORK_REASON)
    return profile


def nextjs_recipe_release_manifest(key: str, release_id: UUID, *, version="1.0.0"):
    if key not in NEXTJS_RECIPES:
        raise ValueError("Unsupported Next.js recipe.")
    manifest = astro_recipe_release_manifest(
        key.replace("nextjs_", "astro_"), release_id, version=version
    )
    return dict(
        manifest,
        recipe_key=key,
        allowed_resource_types=["nextjs_route_metadata"],
        purpose="Resolve one evidence-bound Next.js metadata finding within an exact built scope.",
    )


def _tree(raw, path):
    if not isinstance(raw, bytes) or not 0 <= len(raw) <= 128 * 1024:
        raise TechnicalRecipeUnavailable("NEXT_SOURCE_UNAVAILABLE")
    try:
        source = raw.decode("utf8")
    except UnicodeError:
        raise TechnicalRecipeUnavailable("NEXT_SOURCE_UNAVAILABLE") from None
    if "\x00" in source or "\r" in source:
        raise TechnicalRecipeUnavailable("NEXT_SOURCE_UNAVAILABLE")
    grammar = (
        tree_sitter_typescript.language_tsx()
        if path.endswith((".ts", ".tsx"))
        else tree_sitter_javascript.language()
    )
    tree = Parser(Language(grammar)).parse(raw)
    if tree.root_node.has_error:
        raise TechnicalRecipeUnavailable("NEXT_SOURCE_UNAVAILABLE")
    return tree


def _key(node):
    if node.type == "property_identifier":
        return node.text.decode("utf8")
    if (
        node.type == "string"
        and len(node.named_children) == 1
        and node.named_children[0].type == "string_fragment"
    ):
        return node.text[1:-1].decode("utf8")
    raise TechnicalRecipeUnavailable("NEXT_LITERAL_MAPPING_UNAVAILABLE")


def _static(node, depth=0):
    if depth > 32:
        return False
    if node.type in {"string", "number", "true", "false", "null"}:
        return True
    if node.type == "array":
        return all(
            _static(child, depth + 1) for child in node.named_children if child.type != "comment"
        )
    if node.type == "object":
        keys = []
        for child in node.named_children:
            if child.type == "comment":
                continue
            if child.type != "pair":
                return False
            keys.append(_key(child.child_by_field_name("key")))
            if not _static(child.child_by_field_name("value"), depth + 1):
                return False
        return len(keys) == len(set(keys))
    return False


def _exports(tree, name):
    values = []
    for item in tree.root_node.named_children:
        if item.type != "export_statement":
            continue
        declaration = item.child_by_field_name("declaration")
        if declaration is None:
            continue
        if (
            declaration.type == "function_declaration"
            and declaration.child_by_field_name("name") is not None
            and declaration.child_by_field_name("name").text == name.encode()
        ):
            values.append(declaration)
        elif declaration.type == "lexical_declaration":
            for node in declaration.named_children:
                if (
                    node.type == "variable_declarator"
                    and node.child_by_field_name("name").text == name.encode()
                ):
                    if declaration.children[0].text != b"const":
                        raise TechnicalRecipeUnavailable("NEXT_LITERAL_MAPPING_UNAVAILABLE")
                    values.append(node.child_by_field_name("value"))
    return values


def _bounded_params(tree):
    functions = _exports(tree, "generateStaticParams")
    if len(functions) != 1 or functions[0].type != "function_declaration":
        return False
    function = functions[0]
    parameters = function.child_by_field_name("parameters")
    body = function.child_by_field_name("body")
    statements = [node for node in body.named_children if node.type != "comment"]
    if (
        parameters.named_children
        or len(statements) != 1
        or statements[0].type != "return_statement"
    ):
        return False
    values = statements[0].named_children
    if len(values) != 1 or values[0].type != "array" or not _static(values[0]):
        return False
    entries = [node for node in values[0].named_children if node.type != "comment"]
    return 1 <= len(entries) <= MAX_IMPACT_PAGES and all(node.type == "object" for node in entries)


def _route(files, path):
    if not isinstance(path, str) or not _valid_path(path) or _protected(path) or path not in files:
        raise TechnicalRecipeUnavailable("NEXT_PROTECTED_PATH")
    root = next((root for root in ROOTS if path.startswith(root)), None)
    if root is None or not path.endswith((".tsx", ".jsx")):
        raise TechnicalRecipeUnavailable("NEXT_ROUTE_UNAVAILABLE")
    parts = PurePosixPath(path).parts
    if "api" in parts or any(
        part.startswith(("_", "@", "(")) for part in parts[len(PurePosixPath(root).parts) :]
    ):
        raise TechnicalRecipeUnavailable("NEXT_ROUTE_UNAVAILABLE")
    app = root.endswith("app/")
    if app and PurePosixPath(path).name not in {"page.tsx", "page.jsx", "layout.tsx", "layout.jsx"}:
        raise TechnicalRecipeUnavailable("NEXT_ROUTE_UNAVAILABLE")
    return root, app


def classify_nextjs_scope(files, path, impact_count):
    root, app = _route(files, path)
    if type(impact_count) is not int or not 1 <= impact_count <= MAX_IMPACT_PAGES:
        raise TechnicalRecipeUnavailable("NEXT_IMPACT_UNAVAILABLE")
    dynamic = "[" in path or "]" in path
    layout = app and PurePosixPath(path).stem == "layout"
    if dynamic and (not app or not _bounded_params(_tree(files[path], path))):
        raise TechnicalRecipeUnavailable("NEXT_DYNAMIC_SCOPE_UNAVAILABLE")
    if layout:
        directory = str(PurePosixPath(path).parent) + "/"
        pages = [
            p
            for p in files
            if p.startswith(directory) and PurePosixPath(p).name in {"page.tsx", "page.jsx"}
        ]
        if not 1 <= len(pages) <= MAX_IMPACT_PAGES:
            raise TechnicalRecipeUnavailable("NEXT_LAYOUT_SCOPE_UNAVAILABLE")
        if directory != root and (len(pages) != 1 or impact_count != 1):
            raise TechnicalRecipeUnavailable("NEXT_LAYOUT_SINGLE_PAGE_REQUIRED")
        if any("[" in p or "]" in p for p in pages):
            raise TechnicalRecipeUnavailable("NEXT_LAYOUT_SCOPE_UNAVAILABLE")
        if any(_protected(p) for p in pages):
            raise TechnicalRecipeUnavailable("NEXT_PROTECTED_PATH")
    return "A4" if dynamic or layout or impact_count > 1 else "A2"


def _app_span(tree, field):
    if _exports(tree, "generateMetadata"):
        raise TechnicalRecipeUnavailable("NEXT_GENERATED_METADATA_UNAVAILABLE")
    if any(
        node.type == "expression_statement"
        and node.text in {b'"use client";', b"'use client';", b'"use client"', b"'use client'"}
        for node in tree.root_node.named_children
    ):
        raise TechnicalRecipeUnavailable("NEXT_CLIENT_METADATA_UNAVAILABLE")
    objects = _exports(tree, "metadata")
    if len(objects) != 1 or objects[0].type != "object" or not _static(objects[0]):
        raise TechnicalRecipeUnavailable("NEXT_LITERAL_MAPPING_UNAVAILABLE")
    if any(
        node.type == "identifier"
        and node.text == b"metadata"
        and not (
            node.parent.type == "variable_declarator"
            and node.parent.child_by_field_name("value") == objects[0]
            and node.parent.child_by_field_name("name") == node
        )
        for node in _walk(tree.root_node)
    ):
        raise TechnicalRecipeUnavailable("NEXT_LITERAL_MAPPING_UNAVAILABLE")
    targets = [
        node.child_by_field_name("value")
        for node in objects[0].named_children
        if node.type == "pair" and _key(node.child_by_field_name("key")) == field
    ]
    if len(targets) != 1 or targets[0].type != "string":
        raise TechnicalRecipeUnavailable("NEXT_LITERAL_MAPPING_UNAVAILABLE")
    return targets[0]


def _attributes(node):
    result = {}
    for attribute in node.named_children:
        if attribute.type == "identifier":
            continue
        if attribute.type != "jsx_attribute" or len(attribute.named_children) != 2:
            raise TechnicalRecipeUnavailable("NEXT_LITERAL_MAPPING_UNAVAILABLE")
        key, value = attribute.named_children
        if key.text.decode() in result or value.type != "string":
            raise TechnicalRecipeUnavailable("NEXT_LITERAL_MAPPING_UNAVAILABLE")
        result[key.text.decode()] = value
    return result


def _pages_span(tree, field):
    imports = [
        node
        for node in tree.root_node.named_children
        if node.type == "import_statement"
        and _import_source(node) in {b'"next/head"', b"'next/head'"}
    ]
    if len(imports) != 1:
        raise TechnicalRecipeUnavailable("NEXT_HEAD_MAPPING_UNAVAILABLE")
    clause = next(
        (node for node in imports[0].named_children if node.type == "import_clause"), None
    )
    if (
        clause is None
        or len(clause.named_children) != 1
        or clause.named_children[0].type != "identifier"
    ):
        raise TechnicalRecipeUnavailable("NEXT_HEAD_MAPPING_UNAVAILABLE")
    name = clause.named_children[0].text
    if any(
        node.type == "identifier"
        and node.text == name
        and node.parent.type not in {"import_clause", "jsx_opening_element", "jsx_closing_element"}
        for node in _walk(tree.root_node)
    ):
        raise TechnicalRecipeUnavailable("NEXT_HEAD_MAPPING_UNAVAILABLE")
    heads = [
        node
        for node in _walk(tree.root_node)
        if node.type == "jsx_element" and _tag_name(node.child_by_field_name("open_tag")) == name
    ]
    head_uses = [
        node
        for node in _walk(tree.root_node)
        if node.type in {"jsx_opening_element", "jsx_self_closing_element"}
        and _tag_name(node) == name
    ]
    if len(heads) != 1 or len(head_uses) != 1:
        raise TechnicalRecipeUnavailable("NEXT_HEAD_MAPPING_UNAVAILABLE")
    targets = []
    for node in heads[0].named_children:
        if (
            field == "title"
            and node.type == "jsx_element"
            and _tag_name(node.child_by_field_name("open_tag")) == b"title"
        ):
            children = [
                child
                for child in node.named_children
                if child.type not in {"jsx_opening_element", "jsx_closing_element"}
            ]
            if (
                len(children) == 1
                and children[0].type == "jsx_expression"
                and len(children[0].named_children) == 1
            ):
                targets.append(children[0].named_children[0])
            elif children and all(
                child.type in {"jsx_text", "html_character_reference"} for child in children
            ):
                left = node.child_by_field_name("open_tag").end_byte
                right = node.child_by_field_name("close_tag").start_byte
                targets.append(_LiteralText(left, right, tree.root_node.text[left:right]))
        elif (
            field == "description"
            and node.type == "jsx_self_closing_element"
            and _tag_name(node) == b"meta"
        ):
            attrs = _attributes(node)
            if (
                "name" in attrs
                and attrs["name"].text[1:-1] == b"description"
                and "content" in attrs
            ):
                targets.append(attrs["content"])
    if len(targets) != 1 or targets[0].type not in {"string", "jsx_text"}:
        raise TechnicalRecipeUnavailable("NEXT_LITERAL_MAPPING_UNAVAILABLE")
    return targets[0]


def _tag_name(node):
    name = node.child_by_field_name("name") if node is not None else None
    return name.text if name is not None else None


def _import_source(node):
    source = node.child_by_field_name("source")
    return source.text if source is not None else None


def make_nextjs_recipe_patch(*, files, recipe_key, path, offset, before, after):
    if recipe_key not in NEXTJS_RECIPES:
        raise TechnicalRecipeUnavailable("NEXT_RECIPE_UNAVAILABLE")
    classify_nextjs_scope(files, path, 1)
    _, app = _route(files, path)
    tree = _tree(files[path], path)
    source = files[path].decode("utf8")
    if (
        type(offset) is not int
        or offset < 0
        or not all(
            isinstance(value, str) and 1 <= len(value.encode("utf8")) <= 4096
            for value in (before, after)
        )
        or before == after
        or "\r" in after
        or "\x00" in after
    ):
        raise TechnicalRecipeUnavailable("NEXT_SOURCE_DRIFT")
    span = _app_span if app else _pages_span
    field = recipe_key.removeprefix("nextjs_")
    old = span(tree, field)
    start, end = (
        len(source[:offset].encode("utf8")),
        len(source[: offset + len(before)].encode("utf8")),
    )
    if (old.start_byte, old.end_byte) != (start, end) or old.text.decode("utf8") != before:
        raise TechnicalRecipeUnavailable("NEXT_SOURCE_DRIFT")
    if old.type == "string" and (before[0] != after[0] or before[-1] != after[-1]):
        raise TechnicalRecipeUnavailable("NEXT_LITERAL_STYLE_UNAVAILABLE")
    result = source[:offset] + after + source[offset + len(before) :]
    new = span(_tree(result.encode("utf8"), path), field)
    if (
        new.type != old.type
        or new.start_byte != start
        or new.end_byte != start + len(after.encode("utf8"))
        or new.text.decode("utf8") != after
    ):
        raise TechnicalRecipeUnavailable("NEXT_LITERAL_MAPPING_UNAVAILABLE")
    return RecipePatch(
        path,
        files[path],
        result.encode("utf8"),
        before,
        after,
        offset,
        "Exact declared built pages only; no production publication.",
        "Revert only this exact source fragment in a separate owner-reviewed PR; "
        "conflict on later edits.",
    )


def verify_nextjs_impact(**kwargs):
    key = kwargs.pop("recipe_key")
    if key not in NEXTJS_RECIPES:
        raise TechnicalRecipeUnavailable("NEXT_RECIPE_UNAVAILABLE")
    if PurePosixPath(kwargs["path"]).name in {"layout.tsx", "layout.jsx"} and any(
        len(record.built_html) > MAX_IMPACT_PAGES
        for record in (kwargs["baseline"], kwargs["candidate"])
    ):
        raise TechnicalRecipeUnavailable("NEXT_LAYOUT_SCOPE_UNAVAILABLE")
    return verify_astro_impact(
        **kwargs,
        recipe_key=key.replace("nextjs_", "astro_"),
        scope_classifier=classify_nextjs_scope,
    )
