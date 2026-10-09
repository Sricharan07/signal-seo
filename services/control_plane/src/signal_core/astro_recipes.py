"""Closed Astro source edits and independent exact built-HTML scope verification."""

import hashlib
import re
from dataclasses import dataclass
from html import escape
from uuid import UUID

import rfc8785
import tree_sitter_typescript
from tree_sitter import Language, Parser

from signal_core.astro_source import (
    CONFIGS,
    AstroSourceUnavailable,
    inspect_astro_source,
    strict_json,
)
from signal_core.candidate_build import _protected, _valid_path
from signal_core.technical_seo_recipes import (
    RecipePatch,
    TechnicalRecipeUnavailable,
    _grounded_text,
    _Tokens,
)

ASTRO_RECIPES = {
    "astro_title": "technical_title",
    "astro_description": "technical_description",
    "astro_alt": "technical_alt",
    "astro_json_ld": "technical_structured_data",
}
MAX_IMPACT_PAGES = 128
MAX_SCOPE_BYTES = 2 * 1024 * 1024
MAX_SAMPLE_BYTES = 8192


def astro_recipe_release_manifest(key: str, release_id: UUID, *, version="1.0.0") -> dict:
    if (
        key not in ASTRO_RECIPES
        or not isinstance(release_id, UUID)
        or not re.fullmatch(r"1\.0\.(0|[1-9][0-9]*)", version)
    ):
        raise ValueError("Unsupported Astro recipe release.")
    return {
        "schema_version": 1,
        "kind": "recipe",
        "release_id": str(release_id),
        "recipe_key": key,
        "version": version,
        "contract_version": 1,
        "delivery_mode": "pull_request",
        "allowed_resource_types": ["astro_static_page", "astro_shared_template"],
        "max_resources_per_revision": 1,
        "max_built_pages_per_revision": MAX_IMPACT_PAGES,
        "allowed_fields": [key.removeprefix("astro_")],
        "required_evidence": ["committed_crawl_finding", "paired_offline_builds"],
        "steps": ["one_scoped_patch", "offline_baseline_and_candidate", "exact_html_scope"],
        "verification_assertions": ["exact_base", "protected_path_preflight", "exact_html_scope"],
        "purpose": "Resolve one evidence-bound Astro metadata finding within an exact built scope.",
        "approval_class": "A2_or_A4_owner_fresh_mfa",
        "autonomy_eligible": False,
        "recovery_mode": "revert_exact_patch",
    }


def classify_astro_scope(files: dict[str, bytes], path: str, impact_count: int) -> str:
    """Path and measured reach can only raise risk, never lower a template to a page."""
    if not _valid_path(path) or _protected(path) or path not in files or path in CONFIGS:
        raise TechnicalRecipeUnavailable("ASTRO_PROTECTED_PATH")
    try:
        astro = inspect_astro_source(files)
    except AstroSourceUnavailable:
        raise TechnicalRecipeUnavailable("ASTRO_FORMAT_UNAVAILABLE") from None
    if type(impact_count) is not int or not 1 <= impact_count <= MAX_IMPACT_PAGES:
        raise TechnicalRecipeUnavailable("ASTRO_IMPACT_UNAVAILABLE")
    if path in astro.collection_configs or set(path.split("/")) & {"layouts", "components", "data"}:
        return "A4"
    page = (
        path.startswith(astro.source_directory + "/pages/")
        and path.endswith(".astro")
        and "[" not in path
        and "]" not in path
    )
    collection = path in astro.collection_entries
    return "A2" if impact_count == 1 and (page or collection) else "A4"


def _walk(node):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


def _frontmatter_close(source: str) -> int:
    match = re.search(r"\n---(?:\n|$)", source[4:])
    return match.start() + 4 if match else -1


def _markup_position(source: str, offset: int, length: int, *, insertion=False) -> bool:
    """Admit only parser-proven JSX-compatible markup, never a string in server code.

    HTML void tags and raw script/style bodies are normalized for the existing
    TSX grammar. Unsupported Astro syntax fails closed rather than being guessed.
    """
    body_start = 0
    if source.startswith("---\n"):
        closing = _frontmatter_close(source)
        if closing < 0:
            return False
        body_start = closing + 4
    if offset < body_start:
        return False
    body = source[body_start:]
    tokens = _Tokens(body)
    edits = []
    voids = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    for tag in tokens.tags:
        if tag.name in voids and not tag.raw.endswith("/>"):
            position = len(body[: tag.end - 1].encode("utf8"))
            edits.append((position, position, b"/"))
        if tag.name in {"script", "style"}:
            endings = [a for name, a, _ in tokens.ends if name == tag.name and a >= tag.end]
            if not endings:
                return False
            start, end = len(body[: tag.end].encode("utf8")), len(body[: endings[0]].encode("utf8"))
            edits.append((start, end, b" " * (end - start)))
    raw = body.encode("utf8")
    start = len(body[: offset - body_start].encode("utf8"))
    end = start + len(source[offset : offset + length].encode("utf8"))
    for tag in (b"<!doctype html>", b"<!DOCTYPE html>"):
        raw = raw.replace(tag, b" " * len(tag))
    for left, right, value in sorted(edits, reverse=True):
        raw = raw[:left] + value + raw[right:]
    insertions = [left for left, right, _ in edits if left == right]
    start += sum(position < start for position in insertions)
    end += sum(position < end for position in insertions)
    prefix = b"const __markup = (<>"
    tree = Parser(Language(tree_sitter_typescript.language_tsx())).parse(prefix + raw + b"</>);")
    if tree.root_node.has_error:
        return False
    for node in _walk(tree.root_node):
        if (
            insertion
            and node.type == "jsx_opening_element"
            and node.end_byte == len(prefix) + start
        ):
            name = node.child_by_field_name("name")
            if name is not None and name.text == b"head":
                return True
        if not insertion and node.type in {
            "jsx_element",
            "jsx_opening_element",
            "jsx_self_closing_element",
        }:
            if node.start_byte == len(prefix) + start and node.end_byte == len(prefix) + end:
                return True
    return False


def _safe_expression(node) -> bool:
    if node.type in {"identifier", "property_identifier"}:
        return node.text not in {
            b"process",
            b"globalThis",
            b"window",
            b"document",
            b"require",
        } and not re.search(rb"(secret|password|credential|token|apikey|api_key)", node.text, re.I)
    if node.type in {"string", "string_fragment"}:
        return True
    if node.type == "member_expression":
        if node.text.startswith(b"Astro.") and not re.match(rb"Astro\.props(?:\.|$)", node.text):
            return False
        return all(_safe_expression(child) for child in node.named_children)
    if node.type in {"template_string", "template_substitution"}:
        return all(_safe_expression(child) for child in node.named_children)
    if node.type == "binary_expression" and node.child_by_field_name("operator").text == b"+":
        return all(_safe_expression(child) for child in node.named_children)
    return False


def _metadata_expression(source: str, offset: int, before: str, after: str, field: str) -> bool:
    raw = source.encode("utf8")
    start = len(source[:offset].encode("utf8"))
    end = start + len(before.encode("utf8"))
    if source.startswith("---\n"):
        closing = _frontmatter_close(source)
        if closing < 0 or not 4 <= offset < closing:
            return False
        raw = source[4:closing].encode("utf8")
        start -= 4
        end -= 4
    parser = Parser(Language(tree_sitter_typescript.language_typescript()))
    tree = parser.parse(raw)
    if tree.root_node.has_error:
        return False
    aliases = {"title"} if field == "title" else {"description", "metaDescription"}
    for node in _walk(tree.root_node):
        if node.start_byte != start or node.end_byte != end:
            continue
        parent = node.parent
        if parent.type not in {"variable_declarator", "pair"}:
            continue
        if parent.child_by_field_name("value") != node:
            continue
        name = parent.child_by_field_name("name" if parent.type == "variable_declarator" else "key")
        if name.text.decode("utf8").strip("\"'") not in aliases:
            continue
        replacement = parser.parse(("const value = " + after + ";").encode("utf8"))
        if replacement.root_node.has_error:
            return False
        values = [
            item.child_by_field_name("value")
            for item in _walk(replacement.root_node)
            if item.type == "variable_declarator"
        ]
        return (
            len(replacement.root_node.named_children) == 1
            and len(values) == 1
            and values[0].text.decode("utf8") == after
            and _safe_expression(values[0])
            and _safe_expression(node)
        )
    return False


def _literal_attribute_edit(before: str, after: str, attribute: str) -> bool:
    pattern = r"\s" + attribute + r"\s*=\s*([\"'])(.*?)\1"
    old, new = list(re.finditer(pattern, before, re.S)), list(re.finditer(pattern, after, re.S))
    if len(new) != 1:
        return False
    if len(old) == 1:
        match = old[0]
        return before[: match.start(2)] + new[0][2] + before[match.end(2) :] == after
    if old or re.search(r"\s" + attribute + r"\s*=", before):
        return False
    closing = len(before) - (2 if before.endswith("/>") else 1)
    return before[:closing] + f' {attribute}="' + new[0][2] + '"' + before[closing:] == after


def make_astro_recipe_patch(
    *, files: dict[str, bytes], recipe_key: str, path: str, offset: int, before: str, after: str
) -> RecipePatch:
    """One exact field expression or literal markup; never arbitrary generator code."""
    if recipe_key not in ASTRO_RECIPES:
        raise TechnicalRecipeUnavailable("ASTRO_RECIPE_UNAVAILABLE")
    classify_astro_scope(files, path, 1)
    raw = files[path]
    if not isinstance(raw, bytes) or len(raw) > 128 * 1024:
        raise TechnicalRecipeUnavailable("ASTRO_SOURCE_INVALID")
    try:
        source = raw.decode("utf8")
    except UnicodeError:
        raise TechnicalRecipeUnavailable("ASTRO_SOURCE_INVALID") from None
    if (
        type(offset) is not int
        or offset < 0
        or not isinstance(before, str)
        or not isinstance(after, str)
        or before == after
        or len(before.encode("utf8")) > 4096
        or len(after.encode("utf8")) > 4096
        or source[offset : offset + len(before)] != before
        or "\r" in source + after
        or "\x00" in after
    ):
        raise TechnicalRecipeUnavailable("ASTRO_SOURCE_DRIFT")
    result = source[:offset] + after + source[offset + len(before) :]
    field = recipe_key.removeprefix("astro_")
    valid = False
    if field in {"title", "description"}:
        if path.endswith((".astro", ".ts", ".js", ".mjs", ".mts")):
            valid = _metadata_expression(source, offset, before, after, field)
            if path.endswith(".astro") and not valid:
                tokens = _Tokens(source)
                heads = [tag for tag in tokens.tags if tag.name == "head"]
                if before == "" and len(heads) == 1 and offset == heads[0].end:
                    valid = bool(
                        re.fullmatch(
                            r"<title>[^<>{}]*</title>"
                            if field == "title"
                            else r'<meta name="description" content="[^"<>{}]*">',
                            after,
                        )
                    )
                elif field == "title":
                    valid = bool(
                        re.fullmatch(r"<title>[^<>{}]*</title>", before)
                        and re.fullmatch(r"<title>[^<>{}]*</title>", after)
                    )
            if path.endswith(".astro") and not valid:
                # Literal props on a page/layout component; expression attributes stay unavailable.
                tokens = _Tokens(source[offset:])
                if tokens.tags and tokens.tags[0].start == 0 and before == tokens.tags[0].raw:
                    changed = _Tokens(after)
                    old = tokens.tags[0]
                    if len(changed.tags) == 1 and changed.tags[0].raw == after:
                        new = changed.tags[0]
                        attribute = (
                            "content"
                            if field == "description"
                            and old.name == "meta"
                            and old.attrs.get("name") == "description"
                            else field
                        )
                        old_attrs, new_attrs = dict(old.attrs), dict(new.attrs)
                        old_attrs.pop(attribute, None)
                        value = new_attrs.pop(attribute, None)
                        valid = (
                            old.name == new.name
                            and old_attrs == new_attrs
                            and isinstance(value, str)
                            and "{" not in before + after
                            and "}" not in before + after
                            and _literal_attribute_edit(before, after, attribute)
                        )
        elif path.endswith(".json"):
            old, new = strict_json(raw), strict_json(result.encode("utf8"))
            old.pop(field, None)
            value = new.pop(field, None)
            valid = old == new and isinstance(value, str)
        elif path.endswith((".md", ".mdx", ".yaml", ".yml")):
            # Narrow single scalar line, preserving every other byte and YAML structure.
            valid = bool(
                re.fullmatch(field + r': "[^"\\\r\n<>]*"', after)
                and re.fullmatch(field + r': "[^"\\\r\n<>]*"', before)
                and (offset == 0 or source[offset - 1] == "\n")
                and (offset + len(before) == len(source) or source[offset + len(before)] == "\n")
                and source.count("\n" + field + ":") + int(source.startswith(field + ":")) == 1
                and (
                    not path.endswith((".md", ".mdx"))
                    or source.startswith("---\n")
                    and offset < _frontmatter_close(source)
                )
            )
    elif path.endswith(".astro") and field == "alt":
        old, new = _Tokens(before), _Tokens(after)
        if len(old.tags) == len(new.tags) == 1:
            a, b = old.tags[0], new.tags[0]
            attrs = dict(b.attrs)
            value = attrs.pop("alt", None)
            prior = dict(a.attrs)
            prior.pop("alt", None)
            valid = (
                a.name == b.name == "img"
                and before == a.raw
                and after == b.raw
                and prior == attrs
                and isinstance(value, str)
                and not any(char in before + after for char in "{}")
                and _literal_attribute_edit(before, after, "alt")
            )
    elif path.endswith(".astro") and field == "json_ld":
        tokens = _Tokens(source)
        scripts = [
            tag
            for tag in tokens.tags
            if tag.name == "script" and tag.attrs.get("type") == "application/ld+json"
        ]
        ends = [a for name, a, _ in tokens.ends if name == "script" and a >= offset]
        if (
            len(scripts) == 1
            and ends
            and offset == scripts[0].end
            and offset + len(before) == ends[0]
        ):
            data = strict_json(after.encode("utf8"), 4096)
            valid = (
                set(data) == {"@context", "@type", "url"}
                and data["@context"] == "https://schema.org"
                and data["@type"] == "WebPage"
                and "<" not in after
            )
            if valid:
                valid = _markup_position(source, scripts[0].start, len(scripts[0].raw))
    if (
        valid
        and path.endswith(".astro")
        and not _metadata_expression(source, offset, before, after, field)
        and field != "json_ld"
    ):
        valid = _markup_position(source, offset, len(before), insertion=not before)
    if not valid:
        raise TechnicalRecipeUnavailable("ASTRO_FIELD_MAPPING_UNAVAILABLE")
    return RecipePatch(
        path,
        raw,
        result.encode("utf8"),
        before,
        after,
        offset,
        "Exact declared built pages only; no production publication.",
        "Revert only this exact source fragment in a separate owner-reviewed PR; "
        "conflict on later edits.",
    )


@dataclass(frozen=True)
class AstroImpact:
    approval_class: str
    pages: tuple[str, ...]
    assertions: tuple[dict, ...]
    samples: tuple[dict, ...]

    @property
    def canonical_scope(self) -> bytes:
        return rfc8785.dumps({"assertions": list(self.assertions)})


def _html_change(html: str, key: str, expected: str, page_url: str) -> tuple[int, str, str]:
    tokens = _Tokens(html)
    field = key.removeprefix("astro_")
    candidates = [
        tag
        for tag in tokens.tags
        if field == "title"
        and tag.name == "title"
        or field == "description"
        and tag.name == "meta"
        and tag.attrs.get("name") == "description"
        or field == "alt"
        and tag.name == "img"
        and tag.attrs.get("src") == page_url
        or field == "json_ld"
        and tag.name == "script"
        and tag.attrs.get("type") == "application/ld+json"
    ]
    if not candidates and field in {"title", "description"}:
        heads = [tag for tag in tokens.tags if tag.name == "head"]
        if len(heads) == 1:
            fragment = (
                "<title>" + escape(expected) + "</title>"
                if field == "title"
                else '<meta name="description" content="' + escape(expected, quote=True) + '">'
            )
            return heads[0].end, "", fragment
    if len(candidates) != 1:
        raise TechnicalRecipeUnavailable("ASTRO_HTML_FIELD_AMBIGUOUS")
    tag = candidates[0]
    if field in {"title", "json_ld"}:
        ends = [a for name, a, _ in tokens.ends if name == tag.name and a >= tag.end]
        if not ends:
            raise TechnicalRecipeUnavailable("ASTRO_HTML_FIELD_AMBIGUOUS")
        replacement = escape(expected) if field == "title" else expected
        return tag.end, html[tag.end : ends[0]], replacement
    attribute = "content" if field == "description" else "alt"
    matches = list(re.finditer(r"\s" + attribute + r"\s*=\s*([\"'])(.*?)\1", tag.raw, re.S))
    if field == "alt" and not matches and "alt" not in tag.attrs:
        end = len(tag.raw) - (2 if tag.raw.endswith("/>") else 1)
        return (
            tag.start,
            tag.raw,
            tag.raw[:end] + ' alt="' + escape(expected, quote=True) + '"' + tag.raw[end:],
        )
    if len(matches) != 1:
        raise TechnicalRecipeUnavailable("ASTRO_HTML_FIELD_AMBIGUOUS")
    match = matches[0]
    return tag.start + match.start(2), match[2], escape(expected, quote=True)


def verify_astro_impact(
    *,
    files: dict[str, bytes],
    path: str,
    recipe_key: str,
    baseline,
    candidate,
    expected_by_page: dict[str, str],
    page_urls: dict[str, str],
    image_src: str | None = None,
    scope_classifier=classify_astro_scope,
) -> AstroImpact:
    """Full byte comparison, including non-HTML artifacts; no hash-only or sampled assertion."""
    before, after = dict(baseline.built_html), dict(candidate.built_html)
    if (
        baseline.exit_class != "passed"
        or candidate.exit_class != "passed"
        or not baseline.lockfile_sha256
        or baseline.lockfile_sha256 != candidate.lockfile_sha256
        or baseline.base_sha != candidate.base_sha
        or not before
        or before.keys() != after.keys()
        or not isinstance(expected_by_page, dict)
        or not 1 <= len(expected_by_page) <= MAX_IMPACT_PAGES
        or set(expected_by_page) != set(page_urls)
        or not set(expected_by_page) <= before.keys()
    ):
        raise TechnicalRecipeUnavailable("ASTRO_BUILD_SCOPE_UNAVAILABLE")
    changed = {page for page in before if before[page] != after[page]}
    if changed != expected_by_page.keys():
        raise TechnicalRecipeUnavailable("ASTRO_OUT_OF_SCOPE_HTML")
    b_artifacts = {p: (digest, size) for p, digest, size in baseline.artifacts}
    a_artifacts = {p: (digest, size) for p, digest, size in candidate.artifacts}
    if b_artifacts.keys() != a_artifacts.keys() or any(
        b_artifacts[p] != a_artifacts[p] for p in b_artifacts if p not in changed
    ):
        raise TechnicalRecipeUnavailable("ASTRO_OUT_OF_SCOPE_ARTIFACT")
    assertions, samples = [], []
    sample_bytes = 0
    for page in sorted(changed):
        expected = expected_by_page[page]
        if not isinstance(expected, str):
            raise TechnicalRecipeUnavailable("ASTRO_ASSERTION_INVALID")
        if recipe_key == "astro_json_ld":
            data = strict_json(expected.encode("utf8"), 4096)
            if data != {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "url": page_urls[page],
            }:
                raise TechnicalRecipeUnavailable("ASTRO_JSON_LD_UNGROUNDED")
        else:
            # Claims remain owner-reviewed; deterministic vocabulary grounding still applies.
            text = re.sub(r"<[^>]*>", " ", before[page])
            _grounded_text(
                expected, (text, page_urls[page], image_src or ""), ASTRO_RECIPES[recipe_key]
            )
        offset, old, new = _html_change(
            before[page], recipe_key, expected, image_src or page_urls[page]
        )
        if before[page][:offset] + new + before[page][offset + len(old) :] != after[page]:
            raise TechnicalRecipeUnavailable("ASTRO_UNINTENDED_HTML_CHANGE")
        for html, artifacts in ((before[page], b_artifacts), (after[page], a_artifacts)):
            raw = html.encode("utf8")
            if artifacts.get(page) != (hashlib.sha256(raw).hexdigest(), len(raw)):
                raise TechnicalRecipeUnavailable("ASTRO_HTML_RECEIPT_MISMATCH")
        assertion = {
            "path": page,
            "offset": offset,
            "before": old,
            "after": new,
            "before_sha256": b_artifacts[page][0],
            "after_sha256": a_artifacts[page][0],
        }
        assertions.append(assertion)
        sample = {"path": page, "before": old, "after": new}
        size = len(rfc8785.dumps(sample))
        if len(samples) < 3 and sample_bytes + size <= MAX_SAMPLE_BYTES:
            samples.append(sample)
            sample_bytes += size
    impact = AstroImpact(
        scope_classifier(files, path, len(changed)),
        tuple(sorted(changed)),
        tuple(assertions),
        tuple(samples),
    )
    if len(impact.canonical_scope) > MAX_SCOPE_BYTES or not impact.samples:
        raise TechnicalRecipeUnavailable("ASTRO_SCOPE_LIMIT_EXCEEDED")
    if recipe_key == "astro_json_ld" and (impact.approval_class != "A2" or len(changed) != 1):
        raise TechnicalRecipeUnavailable("ASTRO_JSON_LD_SINGLE_PAGE_REQUIRED")
    return impact
