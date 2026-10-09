"""Closed, grounded JSON-LD builder; distinct from the invalid-WebPage repair."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from uuid import UUID

from signal_core.business_brain import BusinessFact
from signal_core.candidate_build import plan_candidate_build
from signal_core.technical_seo_recipes import (
    RecipePatch,
    TechnicalRecipeUnavailable,
    _one,
    _Tokens,
)

RECIPE_KEY = "structured_data_grounded"
TYPES = ("FAQPage", "Article", "BlogPosting", "Organization", "Product", "BreadcrumbList")
VISIBLE_HTML_TAGS = frozenset(
    "html head title body meta link script template noscript main nav section article header "
    "footer aside address h1 h2 h3 h4 h5 h6 p div span a strong b em i u s small sub sup mark "
    "abbr cite code pre kbd samp var time blockquote q dl dt dd ul ol li figure figcaption img "
    "br hr wbr table caption colgroup col thead tbody tfoot tr th td".split()
)


def structured_data_release_manifest(release_id: UUID, *, version: str = "1.0.0") -> dict:
    if not isinstance(release_id, UUID) or not re.fullmatch(r"1\.0\.(0|[1-9][0-9]*)", version):
        raise ValueError("Invalid grounded structured-data release.")
    return {
        "schema_version": 1,
        "kind": "recipe",
        "release_id": str(release_id),
        "recipe_key": RECIPE_KEY,
        "version": version,
        "contract_version": 1,
        "delivery_mode": "pull_request",
        "max_resources_per_revision": 1,
        "allowed_fields": ["json_ld"],
        "allowed_resource_types": ["static_html_page"],
        "required_evidence": ["committed_crawl_page", "exact_source_body_digest", "approved_facts"],
        "steps": ["closed_grounded_builder", "one_json_ld_block", "isolated_candidate_build"],
        "verification_assertions": ["exact_base", "exact_built_page", "other_artifacts_unchanged"],
        "purpose": "Add or replace one grounded FAQ, article, organization, product or breadcrumb.",
        "approval_class": "owner_review",
        "recovery_mode": "revert_exact_patch",
    }


def site_url(value: object, origin: str) -> str:
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        base = urlsplit(origin)
        if (
            parts is None
            or base.scheme != "https"
            or base.path
            or base.query
            or base.fragment
            or base.username
            or base.password
            or not base.hostname
            or parts.scheme != base.scheme
            or parts.netloc != base.netloc
            or parts.username
            or parts.password
            or parts.fragment
            or parts.query
            or "\\" in value
            or not parts.path.startswith("/")
            or len(value) > 2048
            or any(ord(c) <= 32 for c in value)
        ):
            raise ValueError
    except ValueError:
        raise TechnicalRecipeUnavailable("STRUCTURED_DATA_OFF_SITE_URL") from None
    return value


def static_page_url(path: str, origin: str) -> str:
    if not isinstance(path, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.html", path
    ):
        raise TechnicalRecipeUnavailable("STRUCTURED_DATA_PATH_UNAVAILABLE")
    route = path[:-10] if path == "index.html" or path.endswith("/index.html") else path
    return site_url(origin + "/" + route, origin)


class VisiblePage(HTMLParser):
    """Only plain, non-scripted HTML has provable visibility without a renderer."""

    def __init__(self, source: str, page_url: str):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.stack: list[tuple[str, bool]] = []
        self.text: list[str] = []
        self.titles: list[str] = []
        self.headlines: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.parts: list[str] = []
        self.capture: tuple[str, str | None] | None = None
        self.feed(source)
        self.close()
        if self.stack:
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_HTML_AMBIGUOUS")

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if len(attrs) != len(values):
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_HTML_AMBIGUOUS")
        if (
            tag not in VISIBLE_HTML_TAGS
            or "popover" in values
            or any(key.startswith("on") for key in values)
        ):
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_RENDER_EVIDENCE_REQUIRED")
        if tag == "link" and "stylesheet" in (values.get("rel") or "").casefold().split():
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_RENDER_EVIDENCE_REQUIRED")
        if tag == "script" and (values.get("type") or "").lower() != "application/ld+json":
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_RENDER_EVIDENCE_REQUIRED")
        style = (values.get("style") or "").replace(" ", "").lower()
        if style:
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_RENDER_EVIDENCE_REQUIRED")
        hidden = (
            any(h for _, h in self.stack)
            or tag in {"script", "template", "noscript", "head"}
            or "hidden" in values
            or (values.get("aria-hidden") or "").lower() == "true"
        )
        if tag not in {
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
        }:
            self.stack.append((tag, hidden))
        if tag == "title" or (not hidden and tag in {"h1", "a"}):
            if self.capture is not None:
                raise TechnicalRecipeUnavailable("STRUCTURED_DATA_HTML_AMBIGUOUS")
            self.capture, self.parts = (tag, values.get("href")), []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_HTML_AMBIGUOUS")
        self.stack.pop()
        if self.capture and self.capture[0] == tag:
            value = "".join(self.parts).strip()
            if tag == "title":
                self.titles.append(value)
            elif tag == "h1":
                self.headlines.append(value)
            elif self.capture[1]:
                self.links.append((value, urljoin(self.page_url, self.capture[1])))
            self.capture = None

    def handle_data(self, data):
        hidden = any(h for _, h in self.stack)
        if not hidden:
            self.text.append(data)
        if self.capture and (self.capture[0] == "title" or not hidden):
            self.parts.append(data)

    def verbatim(self, value: object) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 1000 or value != value.strip():
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_UNGROUNDED")
        # Do not match across hidden regions or turn arbitrary substrings into assertions.
        if value not in [part.strip() for part in self.text]:
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_VERBATIM_REQUIRED")
        return value


@dataclass(frozen=True)
class GroundedBlock:
    document: dict
    fact_refs: dict[str, str]
    owner_required: bool


def build_grounded_block(
    proposal: dict,
    *,
    source: str,
    page_url: str,
    origin: str,
    facts: tuple[BusinessFact, ...] = (),
) -> GroundedBlock:
    site_url(page_url, origin)
    if not isinstance(proposal, dict) or proposal.get("@type") not in TYPES:
        raise TechnicalRecipeUnavailable("STRUCTURED_DATA_TYPE_UNSUPPORTED")
    kind = proposal["@type"]
    page = VisiblePage(source, page_url)
    result = {"@context": "https://schema.org", "@type": kind, "url": page_url}
    references = {}
    if "url" in proposal and site_url(proposal["url"], origin) != page_url:
        raise TechnicalRecipeUnavailable("STRUCTURED_DATA_UNGROUNDED")
    if kind == "FAQPage":
        pairs = proposal.get("questions")
        if not isinstance(pairs, list) or not 1 <= len(pairs) <= 8:
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_FAQ_UNAVAILABLE")
        result["mainEntity"] = []
        for pair in pairs:
            if not isinstance(pair, dict) or set(pair) != {"question", "answer"}:
                raise TechnicalRecipeUnavailable("STRUCTURED_DATA_FAQ_UNAVAILABLE")
            result["mainEntity"].append(
                {
                    "@type": "Question",
                    "name": page.verbatim(pair["question"]),
                    "acceptedAnswer": {"@type": "Answer", "text": page.verbatim(pair["answer"])},
                }
            )
    elif kind in {"Article", "BlogPosting"}:
        headline = proposal.get("headline") or next(iter(page.titles + page.headlines), None)
        if (
            not isinstance(headline, str)
            or not 1 <= len(headline) <= 1000
            or headline not in page.titles + page.headlines
            or not headline
        ):
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_HEADLINE_UNGROUNDED")
        result["headline"] = headline
        for field in ("datePublished", "dateModified"):
            value = proposal.get(field)
            if value is None:
                continue
            page.verbatim(value)
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[0-9:+.Z-]+)?", value):
                    raise ValueError
                (datetime.fromisoformat if "T" in value else date.fromisoformat)(value)
            except (ValueError, TypeError):
                raise TechnicalRecipeUnavailable("STRUCTURED_DATA_DATE_INVALID") from None
            result[field] = value
    elif kind in {"Organization", "Product"}:
        result.pop("url")
        approved = {str(f.fact_id): f for f in facts if f.status == "approved"}
        refs = proposal.get("fact_refs", {})
        if not isinstance(refs, dict):
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_FACT_UNAVAILABLE")
        for field in ("name", "description", "url"):
            if field not in refs:
                continue
            fact = approved.get(refs[field]) if isinstance(refs[field], str) else None
            if fact is None or not 1 <= len(fact.statement) <= 1000:
                raise TechnicalRecipeUnavailable("STRUCTURED_DATA_FACT_UNAVAILABLE")
            result[field] = fact.statement
            references[field] = str(fact.fact_id)
            if field == "url":
                site_url(fact.statement, origin)
        if "name" not in references:
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_FACT_UNAVAILABLE")
        # No prices, offers, ratings, brand or arbitrary claims in this closed release.
    else:
        crumbs = proposal.get("items")
        if not isinstance(crumbs, list) or not 1 <= len(crumbs) <= 8:
            raise TechnicalRecipeUnavailable("STRUCTURED_DATA_BREADCRUMB_UNAVAILABLE")
        result.pop("url")
        result["itemListElement"] = []
        for position, item in enumerate(crumbs, 1):
            if (
                not isinstance(item, dict)
                or set(item) != {"name", "item"}
                or not isinstance(item["name"], str)
                or not 1 <= len(item["name"]) <= 1000
            ):
                raise TechnicalRecipeUnavailable("STRUCTURED_DATA_BREADCRUMB_UNAVAILABLE")
            url = site_url(item["item"], origin)
            if (item["name"], url) not in page.links:
                raise TechnicalRecipeUnavailable("STRUCTURED_DATA_BREADCRUMB_UNGROUNDED")
            result["itemListElement"].append({"@type": "ListItem", "position": position, **item})
    block = GroundedBlock(result, references, kind in {"Organization", "Product"})
    if len(json.dumps(result).encode()) > 3000:
        raise TechnicalRecipeUnavailable("STRUCTURED_DATA_BLOCK_TOO_LARGE")
    return block


def make_structured_data_patch(
    *, evidence, extension, checkout, proposal, source_path="index.html", facts=()
):
    origin, page_url = evidence["site_origin"], evidence["page_url"]
    if (
        extension.framework != "eleventy"
        or extension.content_format != "html"
        or evidence.get("page_output_truncated")
        or static_page_url(source_path, origin) != page_url
    ):
        raise TechnicalRecipeUnavailable("STRUCTURED_DATA_FORMAT_UNAVAILABLE")
    before = dict(checkout.files).get(source_path)
    if not isinstance(before, bytes) or hashlib.sha256(before).hexdigest() != evidence.get(
        "page_body_sha256"
    ):
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
    try:
        source = before.decode("utf-8")
    except UnicodeDecodeError:
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_UNAVAILABLE") from None
    if any(marker in source for marker in ("{{", "{%", "<%", "${")):
        raise TechnicalRecipeUnavailable("RECIPE_TEMPLATE_UNAVAILABLE")
    block = build_grounded_block(
        proposal, source=source, page_url=page_url, origin=origin, facts=facts
    )
    tokens = _Tokens(source)
    head = _one([t for t in tokens.tags if t.name == "head"], "RECIPE_HEAD_UNAVAILABLE")
    scripts = [
        t
        for t in tokens.tags
        if t.name == "script" and (t.attrs.get("type") or "").lower() == "application/ld+json"
    ]
    if len(scripts) > 1:
        raise TechnicalRecipeUnavailable("RECIPE_STRUCTURED_DATA_AMBIGUOUS")
    encoded = (
        json.dumps(block.document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    if len(encoded.encode()) > 3000:
        raise TechnicalRecipeUnavailable("STRUCTURED_DATA_BLOCK_TOO_LARGE")
    replacement = '<script type="application/ld+json">' + encoded + "</script>"
    start = end = head.end
    if scripts:
        script = scripts[0]
        endings = [(a, b) for name, a, b in tokens.ends if name == "script" and a >= script.end]
        if len(endings) != 1:
            raise TechnicalRecipeUnavailable("RECIPE_STRUCTURED_DATA_AMBIGUOUS")
        start, end = script.start, endings[0][1]
    after = (source[:start] + replacement + source[end:]).encode()
    if before == after:
        raise TechnicalRecipeUnavailable("RECIPE_NO_CHANGE")
    plan_candidate_build(
        extension, checkout, patch={source_path: after}, approved_paths=frozenset({source_path})
    )
    return RecipePatch(
        source_path,
        before,
        after,
        source[start:end],
        replacement,
        len(source[:start].encode()),
        "API citations may show an observed change; no causal or ranking improvement is promised.",
        "Revert only this exact JSON-LD block against the recorded base commit.",
    ), block


def assert_structured_build(patch, baseline, candidate, *, output_path: str):
    before, after = (
        dict((p, (h, n)) for p, h, n in baseline.artifacts),
        dict((p, (h, n)) for p, h, n in candidate.artifacts),
    )
    if (
        baseline.exit_class != "passed"
        or candidate.exit_class != "passed"
        or before.pop(output_path, None)
        != (hashlib.sha256(patch.before).hexdigest(), len(patch.before))
        or after.pop(output_path, None)
        != (hashlib.sha256(patch.after).hexdigest(), len(patch.after))
        or before != after
        or len(before) + 1 != len(baseline.artifacts)
        or len(after) + 1 != len(candidate.artifacts)
    ):
        raise TechnicalRecipeUnavailable("STRUCTURED_DATA_BUILT_OUTPUT_MISMATCH")
