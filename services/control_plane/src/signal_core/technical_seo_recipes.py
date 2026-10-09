"""Closed technical SEO edits for one evidence-bound static HTML page."""

import hashlib
import json
import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin
from uuid import UUID

from signal_core.candidate_build import CandidatePolicyRejected, plan_candidate_build
from signal_core.crawl_parser import parse_crawl_page
from signal_core.github_app import GitHubRepositoryCheckout
from signal_core.github_pr_extension import GitHubPrExtension
from signal_core.indexnow_protocol import KEY_RECIPE

RECIPE_FINDING_KEYS = {
    "technical_title": frozenset({"metadata.title.missing", "metadata.title.duplicate"}),
    "technical_description": frozenset(
        {"metadata.meta_description.missing", "metadata.meta_description.duplicate"}
    ),
    "technical_alt": frozenset({"images.alt.missing"}),
    "technical_canonical": frozenset({"canonical.missing"}),
    "technical_structured_data": frozenset({"structured_data.invalid_json_ld"}),
    "technical_broken_link": frozenset({"links.internal.not_found"}),
}
_TEXT_RECIPES = frozenset({"technical_title", "technical_description", "technical_alt"})
_FIELD = {
    "technical_internal_link_add": "internal_link_add",
    "technical_title": "title",
    "technical_description": "meta_description",
    "technical_alt": "image_alt",
    "technical_canonical": "canonical",
    "technical_structured_data": "json_ld",
    "technical_broken_link": "internal_link",
}
_WORD = re.compile(r"[\w]+", re.UNICODE)
_SAFE_WORDS = frozenset(
    "a an and at by for from in of on or the this to with page image photo learn "
    "about discover view see more information website".split()
)


class TechnicalRecipeUnavailable(Exception):
    """The evidence, format, source, or narrowly scoped edit is unavailable."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RecipePatch:
    path: str
    before: bytes
    after: bytes
    before_fragment: str
    after_fragment: str
    offset: int
    expected_impact: str
    recovery_plan: str

    @property
    def patch(self) -> dict[str, bytes]:
        return {self.path: self.after}


def technical_recipe_release_manifest(
    recipe_key: str, release_id: UUID, *, version: str = "1.0.0"
) -> dict:
    """Fixed reviewed contract; release registration still requires a platform signer."""
    if (
        recipe_key not in {*RECIPE_FINDING_KEYS, KEY_RECIPE, "technical_internal_link_add"}
        or not isinstance(release_id, UUID)
        or not re.fullmatch(r"1\.0\.(0|[1-9][0-9]*)", version)
    ):
        raise ValueError("Unsupported technical recipe release.")
    if recipe_key == KEY_RECIPE:
        return {
            "schema_version": 1,
            "kind": "recipe",
            "release_id": str(release_id),
            "recipe_key": recipe_key,
            "version": version,
            "contract_version": 1,
            "delivery_mode": "pull_request",
            "max_resources_per_revision": 1,
            "allowed_fields": ["indexnow_key_file"],
            "allowed_resource_types": ["static_root_text_file"],
            "required_evidence": ["verified_site", "openbao_key_digest"],
            "steps": ["one_root_file_addition", "isolated_candidate_build", "owner_inbox_review"],
            "verification_assertions": [
                "exact_base",
                "protected_path_preflight",
                "key_artifact_exact",
                "build_passed",
            ],
            "purpose": "Publish one site-bound IndexNow key file through an owner-reviewed PR.",
            "approval_class": "owner_review",
            "recovery_mode": "retire_key_generation_new_pr",
        }
    manifest = {
        "schema_version": 1,
        "kind": "recipe",
        "release_id": str(release_id),
        "recipe_key": recipe_key,
        "version": version,
        "contract_version": 1,
        "delivery_mode": "pull_request",
        "max_resources_per_revision": 1,
        "allowed_fields": [_FIELD[recipe_key]],
        "allowed_resource_types": ["static_html_homepage"],
        "required_evidence": ["committed_crawl_finding", "exact_source_body_digest"],
        "steps": ["one_scoped_patch", "isolated_candidate_build"],
        "verification_assertions": ["exact_base", "protected_path_preflight", "build_passed"],
        "purpose": "Resolve one committed technical SEO finding on one exact static page.",
        "approval_class": "owner_review",
        "recovery_mode": "revert_exact_patch",
    }
    if recipe_key == "technical_internal_link_add":
        manifest.update(
            allowed_resource_types=["static_html_page"],
            required_evidence=["committed_crawl_graph", "exact_source_body_digest"],
            verification_assertions=[
                "exact_base",
                "protected_path_preflight",
                "build_passed",
                "one_existing_text_link_only",
            ],
            max_new_links_per_page_per_cycle=3,
            autonomy_eligible=False,
            purpose="Wrap existing relevant paragraph text in exactly one verified-site link.",
        )
    return manifest


@dataclass(frozen=True)
class _Tag:
    name: str
    attrs: dict[str, str | None]
    start: int
    end: int
    raw: str


class _Tokens(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.starts = [0]
        for match in re.finditer("\n", source):
            self.starts.append(match.end())
        self.tags: list[_Tag] = []
        self.ends: list[tuple[str, int, int]] = []
        self.feed(source)
        self.close()

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.starts[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text()
        start = self._offset()
        self.tags.append(_Tag(tag, dict(attrs), start, start + len(raw), raw))

    def handle_endtag(self, tag: str) -> None:
        start = self._offset()
        match = re.match(r"</\s*[a-zA-Z0-9:-]+\s*>", self.source[start:])
        if match:
            self.ends.append((tag, start, start + len(match.group())))


def _one(items: list[_Tag], code: str) -> _Tag:
    if len(items) != 1:
        raise TechnicalRecipeUnavailable(code)
    return items[0]


def _grounded_text(value: str, facts: tuple[str, ...], recipe_key: str) -> str:
    limit = {"technical_title": 70, "technical_description": 160, "technical_alt": 125}[recipe_key]
    minimum = 1 if recipe_key != "technical_description" else 70
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= limit
        or value != value.strip()
        or any(ord(char) < 32 for char in value)
        or "<" in value
        or ">" in value
    ):
        raise TechnicalRecipeUnavailable("RECIPE_DRAFT_INVALID")
    evidence_words = set(_WORD.findall(" ".join(facts).casefold()))
    unsupported = set(_WORD.findall(value.casefold())) - evidence_words - _SAFE_WORDS
    if unsupported:
        raise TechnicalRecipeUnavailable("RECIPE_OWNER_CLAIM_REVIEW_REQUIRED")
    return value


def make_technical_recipe_patch(
    *,
    recipe_key: str,
    evidence: dict,
    extension: GitHubPrExtension,
    checkout: GitHubRepositoryCheckout,
    drafted_text: str | None = None,
) -> RecipePatch:
    """Edit only the owner's exact static homepage when crawl bytes match source bytes."""
    finding = evidence.get("finding")
    if not isinstance(finding, dict) or finding.get("key") not in RECIPE_FINDING_KEYS.get(
        recipe_key, frozenset()
    ):
        raise TechnicalRecipeUnavailable("RECIPE_FINDING_UNAVAILABLE")
    if evidence.get("page_output_truncated") is True:
        raise TechnicalRecipeUnavailable("RECIPE_EVIDENCE_INCOMPLETE")
    if (
        extension.framework != "eleventy"
        or extension.content_format != "html"
        or checkout.inventory.snapshot.content_path != "index.html"
        or evidence.get("page_url") != evidence.get("site_origin", "").rstrip("/") + "/"
    ):
        raise TechnicalRecipeUnavailable("RECIPE_FORMAT_UNAVAILABLE")
    path = checkout.inventory.snapshot.content_path
    before = dict(checkout.files).get(path)
    if not isinstance(before, bytes) or hashlib.sha256(before).hexdigest() != evidence.get(
        "page_body_sha256"
    ):
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
    if b"\r" in before or len(before) > 128 * 1024:
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_UNAVAILABLE")
    try:
        source = before.decode("utf-8")
    except UnicodeDecodeError:
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_UNAVAILABLE") from None
    if any(marker in source for marker in ("{{", "{%", "<%", "${")):
        raise TechnicalRecipeUnavailable("RECIPE_TEMPLATE_UNAVAILABLE")
    tokens = _Tokens(source)
    head = _one([tag for tag in tokens.tags if tag.name == "head"], "RECIPE_HEAD_UNAVAILABLE")
    if head.end >= len(source) or not any(tag == "head" for tag, _, _ in tokens.ends):
        raise TechnicalRecipeUnavailable("RECIPE_HEAD_UNAVAILABLE")
    page_url = evidence["page_url"]
    headings = tuple(
        item["text"]
        for item in evidence.get("page_headings", [])
        if isinstance(item, dict) and item.get("level") == 1 and isinstance(item.get("text"), str)
    )
    if recipe_key in _TEXT_RECIPES:
        if drafted_text is None:
            raise TechnicalRecipeUnavailable("RECIPE_MODEL_UNAVAILABLE")
        facts = (page_url, evidence.get("page_title") or "", *headings)
        if recipe_key == "technical_alt":
            facts += tuple(evidence.get("missing_alt_images") or ())
        drafted_text = _grounded_text(drafted_text, facts, recipe_key)
    key = finding["key"]
    start: int
    end: int
    replacement: str
    if recipe_key == "technical_title":
        titles = [tag for tag in tokens.tags if tag.name == "title"]
        if key.endswith("missing"):
            if evidence.get("page_title") or titles:
                raise TechnicalRecipeUnavailable("RECIPE_FINDING_STALE")
            start = end = head.end
            replacement = "<title>" + escape(drafted_text) + "</title>"
        else:
            title = _one(titles, "RECIPE_TITLE_AMBIGUOUS")
            if not evidence.get("page_title"):
                raise TechnicalRecipeUnavailable("RECIPE_FINDING_STALE")
            matching = [(a, b) for tag, a, b in tokens.ends if tag == "title" and a >= title.end]
            if len(matching) != 1:
                raise TechnicalRecipeUnavailable("RECIPE_TITLE_AMBIGUOUS")
            start, end = title.end, matching[0][0]
            replacement = escape(drafted_text)
    elif recipe_key == "technical_description":
        descriptions = [
            tag
            for tag in tokens.tags
            if tag.name == "meta" and (tag.attrs.get("name") or "").casefold() == "description"
        ]
        if key.endswith("missing"):
            if evidence.get("page_description") or descriptions:
                raise TechnicalRecipeUnavailable("RECIPE_FINDING_STALE")
            start = end = head.end
            replacement = (
                '<meta name="description" content="' + escape(drafted_text, quote=True) + '">'
            )
        else:
            tag = _one(descriptions, "RECIPE_DESCRIPTION_AMBIGUOUS")
            if not evidence.get("page_description"):
                raise TechnicalRecipeUnavailable("RECIPE_FINDING_STALE")
            start, end = tag.start, tag.end
            replacement = (
                '<meta name="description" content="' + escape(drafted_text, quote=True) + '">'
            )
    elif recipe_key == "technical_alt":
        missing = evidence.get("missing_alt_images") or []
        if not isinstance(missing, list) or len(missing) != 1:
            raise TechnicalRecipeUnavailable("RECIPE_IMAGE_AMBIGUOUS")
        images = [
            tag
            for tag in tokens.tags
            if tag.name == "img" and tag.attrs.get("src") == missing[0] and "alt" not in tag.attrs
        ]
        tag = _one(images, "RECIPE_IMAGE_AMBIGUOUS")
        start, end = tag.start, tag.end
        position = tag.raw.rfind("/>") if tag.raw.endswith("/>") else tag.raw.rfind(">")
        replacement = (
            tag.raw[:position]
            + ' alt="'
            + escape(drafted_text, quote=True)
            + '"'
            + tag.raw[position:]
        )
    elif recipe_key == "technical_canonical":
        if evidence.get("page_canonical"):
            raise TechnicalRecipeUnavailable("RECIPE_FINDING_STALE")
        if any(
            tag.name == "link" and "canonical" in (tag.attrs.get("rel") or "").casefold().split()
            for tag in tokens.tags
        ):
            raise TechnicalRecipeUnavailable("RECIPE_CANONICAL_AMBIGUOUS")
        start = end = head.end
        replacement = '<link rel="canonical" href="' + escape(page_url, quote=True) + '">'
    elif recipe_key == "technical_structured_data":
        if not evidence.get("page_parse_error_count", 0):
            raise TechnicalRecipeUnavailable("RECIPE_FINDING_STALE")
        scripts = [
            tag
            for tag in tokens.tags
            if tag.name == "script"
            and (tag.attrs.get("type") or "").lower() == "application/ld+json"
        ]
        tag = _one(scripts, "RECIPE_STRUCTURED_DATA_AMBIGUOUS")
        endings = [(a, b) for name, a, b in tokens.ends if name == "script" and a >= tag.end]
        if len(endings) != 1:
            raise TechnicalRecipeUnavailable("RECIPE_STRUCTURED_DATA_AMBIGUOUS")
        start, end = tag.end, endings[0][0]
        try:
            json.loads(source[start:end])
        except json.JSONDecodeError:
            pass
        else:
            raise TechnicalRecipeUnavailable("RECIPE_FINDING_STALE")
        replacement = json.dumps(
            {"@context": "https://schema.org", "@type": "WebPage", "url": page_url},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    elif recipe_key == "technical_broken_link":
        target = finding.get("resource_locator")
        if target not in (evidence.get("page_internal_links") or []):
            raise TechnicalRecipeUnavailable("RECIPE_FINDING_STALE")
        links = [
            tag
            for tag in tokens.tags
            if tag.name == "a"
            and tag.attrs.get("href")
            and urljoin(page_url, tag.attrs["href"]) == target
        ]
        tag = _one(links, "RECIPE_LINK_AMBIGUOUS")
        start, end = tag.start, tag.end
        replacement = re.sub(r"\s+href\s*=\s*(['\"]).*?\1", "", tag.raw, count=1, flags=re.I | re.S)
        if replacement == tag.raw:
            raise TechnicalRecipeUnavailable("RECIPE_LINK_AMBIGUOUS")
    else:
        raise TechnicalRecipeUnavailable("RECIPE_UNAVAILABLE")
    after_text = source[:start] + replacement + source[end:]
    after = after_text.encode("utf-8")
    if after == before:
        raise TechnicalRecipeUnavailable("RECIPE_NO_CHANGE")
    parsed = parse_crawl_page(after, page_url=page_url, exact_origin=evidence["site_origin"])
    verified = {
        "technical_title": lambda: parsed.title == drafted_text,
        "technical_description": lambda: parsed.meta_description == drafted_text,
        "technical_alt": lambda: missing[0] not in parsed.missing_alt_images,
        "technical_canonical": lambda: parsed.canonical_url == page_url,
        "technical_structured_data": lambda: parsed.parse_error_count == 0,
        "technical_broken_link": lambda: target not in parsed.internal_links,
    }
    if parsed.output_truncated or not verified[recipe_key]():
        raise TechnicalRecipeUnavailable("RECIPE_POSTCONDITION_FAILED")
    try:
        plan_candidate_build(
            extension, checkout, patch={path: after}, approved_paths=frozenset({path})
        )
    except CandidatePolicyRejected:
        raise TechnicalRecipeUnavailable("RECIPE_PROTECTED_PATH") from None
    return RecipePatch(
        path,
        before,
        after,
        source[start:end],
        replacement,
        len(source[:start].encode("utf-8")),
        "Resolve one evidence-linked technical SEO finding; effect requires live verification.",
        "Revert the exact patch against the recorded base commit if live verification fails.",
    )
