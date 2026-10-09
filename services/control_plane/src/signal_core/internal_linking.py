"""Deterministic crawl graph and exact existing-text paragraph links; no I/O."""

import hashlib
import re
from collections import Counter
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from signal_core.candidate_build import CandidatePolicyRejected, plan_candidate_build
from signal_core.structured_data_recipe import site_url, static_page_url
from signal_core.technical_seo_recipes import RecipePatch, TechnicalRecipeUnavailable

RECIPE = "technical_internal_link_add"
DEFAULT_PAGE_CAP = 3
_WORDS = re.compile(r"[a-zA-Z][a-zA-Z0-9-]{2,}")
_STOP = frozenset(
    "the and for with from this that have your our more about into are was were "
    "you not but can all page home guide learn read click here".split()
)
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_BLOCKED = frozenset("nav footer header aside script style template noscript form button a".split())


def _url(value, origin):
    if not isinstance(value, str) or not isinstance(origin, str):
        return None
    try:
        parsed = urlsplit(value)
        value = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
        site_url(value, origin)
        if (
            parsed.scheme != "https"
            or parsed.netloc != urlsplit(origin).netloc
            or parsed.username
            or parsed.password
            or parsed.query
        ):
            return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
    except (ValueError, TechnicalRecipeUnavailable):
        return None


def salient_terms(page):
    text = " ".join([page.get("title") or "", *(h["text"] for h in page.get("headings", []))])
    counts = Counter(w.casefold() for w in _WORDS.findall(text) if w.casefold() not in _STOP)
    return tuple(sorted(counts, key=lambda word: (-counts[word], word))[:32])


def build_link_graph(pages, origin):
    """Counts distinct observed referring pages, never asserts whole-site coverage."""
    nodes = {}
    for page in sorted(pages, key=lambda p: p["id"]):
        url = _url(page["url"], origin)
        if url and not page.get("output_truncated") and url not in nodes:
            nodes[url] = {**page, "url": url, "terms": salient_terms(page)}
    edges = {
        (source, target)
        for source, page in nodes.items()
        for href in page.get("internal_links", [])
        if (target := _url(urljoin(source, href), origin)) in nodes and target != source
    }
    incoming = {url: sum(target == url for _, target in edges) for url in nodes}
    return {"nodes": nodes, "edges": tuple(sorted(edges)), "incoming": incoming}


def link_opportunities(pages, origin, *, page_cap=DEFAULT_PAGE_CAP):
    if type(page_cap) is not int or not 1 <= page_cap <= DEFAULT_PAGE_CAP:
        raise ValueError("Page cap must be between one and three.")
    graph = build_link_graph(pages, origin)
    proposals = []
    counts = Counter()
    selected = set(graph["edges"])
    for target, incoming in sorted(graph["incoming"].items(), key=lambda pair: (pair[1], pair[0])):
        if incoming > 1 or target == origin.rstrip("/") + "/":
            continue
        for source, page in sorted(graph["nodes"].items()):
            shared = sorted(set(page["terms"]) & set(graph["nodes"][target]["terms"]))
            if (
                source == target
                or (source, target) in selected
                or (target, source) in selected
                or len(shared) < 2
                or counts[source] >= page_cap
            ):
                continue
            proposals.append(
                {
                    "source_id": page["id"],
                    "target_id": graph["nodes"][target]["id"],
                    "source_url": source,
                    "target_url": target,
                    "incoming_count": incoming,
                    "status": "no_incoming_observed" if incoming == 0 else "weakly_linked",
                    "shared_terms": shared,
                    "relatedness": len(shared),
                    "page_cap": page_cap,
                    "recipe_key": RECIPE,
                    "autonomy_eligible": False,
                }
            )
            counts[source] += 1
            selected.add((source, target))
    return proposals


class _Paragraphs(HTMLParser):
    """Conservatively accept plain-text paragraphs in explicit main/article content."""

    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.starts = [0, *(m.end() for m in re.finditer("\n", source))]
        self.stack = []
        self.paragraph = None
        self.ranges = []
        self.invalid = False
        self.links = []
        self.feed(source)
        self.close()
        if self.stack:
            self.invalid = True

    def source_offset(self):
        line, column = self.getpos()
        return self.starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if len(attrs) != len(values) or any(key.startswith("on") for key in values):
            self.invalid = True
        attrs = values
        labels = " ".join(str(attrs.get(k) or "") for k in ("class", "id", "role"))
        blocked = (
            tag in _BLOCKED
            or bool(
                re.search(
                    r"nav|footer|header|boilerplate|menu|sidebar|related|breadcrumb|copyright",
                    labels,
                    re.I,
                )
            )
            or "hidden" in attrs
            or "style" in attrs
            or attrs.get("aria-hidden") == "true"
        )
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if self.paragraph is not None:
            self.paragraph = (*self.paragraph[:1], False)
        if tag == "p":
            allowed = any(t in {"main", "article"} for t, _ in self.stack)
            allowed = allowed and not blocked and not any(b for _, b in self.stack)
            self.paragraph = (self.source_offset() + len(self.get_starttag_text()), allowed)
        if tag not in _VOID:
            self.stack.append((tag, blocked))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            self.invalid = True
            return
        if tag == "p" and self.paragraph:
            start, allowed = self.paragraph
            if allowed:
                self.ranges.append((start, self.source_offset()))
            self.paragraph = None
        self.stack.pop()


class _Anchors(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.current = None
        self.anchors = []
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.anchors.append(" ".join("".join(self.current).split()))
            self.current = None


def make_internal_link_patch(
    *,
    evidence,
    extension,
    checkout,
    opportunity,
    used_anchors=(),
    source_path="index.html",
    selected_anchor=None,
):
    origin = evidence["site_origin"]
    source_url = evidence["page_url"]
    target = opportunity["target_url"]
    if _url(target, origin) != target or source_url == target:
        raise TechnicalRecipeUnavailable("INTERNAL_LINK_TARGET_REFUSED")
    if (
        extension.framework != "eleventy"
        or extension.content_format != "html"
        or static_page_url(source_path, origin) != source_url
        or evidence.get("page_output_truncated")
        or opportunity["source_id"] != evidence["page_id"]
        or opportunity["source_url"] != source_url
    ):
        raise TechnicalRecipeUnavailable("RECIPE_FORMAT_UNAVAILABLE")
    before = dict(checkout.files).get(source_path)
    if (
        not isinstance(before, bytes)
        or hashlib.sha256(before).hexdigest() != evidence["page_body_sha256"]
    ):
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
    try:
        source = before.decode("utf-8")
    except UnicodeDecodeError:
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_UNAVAILABLE") from None
    if (
        b"\r" in before
        or len(before) > 128 * 1024
        or any(s in source for s in ("{{", "{%", "<%", "${"))
    ):
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_UNAVAILABLE")
    parser = _Paragraphs(source)
    if parser.invalid or any(
        _url(urljoin(source_url, href), origin) == target for href in parser.links
    ):
        raise TechnicalRecipeUnavailable("INTERNAL_LINK_DUPLICATE_OR_AMBIGUOUS")
    used = {a.casefold() for a in used_anchors}
    paragraphs = Counter()
    for path, content in checkout.files:
        if path.endswith(".html"):
            try:
                text = content.decode("utf-8")
                used.update(a.casefold() for a in _Anchors(text).anchors)
                document = _Paragraphs(text)
                paragraphs.update(" ".join(text[s:e].split()) for s, e in document.ranges)
            except UnicodeDecodeError:
                raise TechnicalRecipeUnavailable("RECIPE_SOURCE_UNAVAILABLE") from None
    shared = set(opportunity["shared_terms"])
    choices = []
    for start, end in parser.ranges:
        paragraph = source[start:end]
        # Entity references and markup are not split or rewritten by this recipe.
        if (
            "&" in paragraph
            or "<" in paragraph
            or len(paragraph) < 40
            or paragraphs[" ".join(paragraph.split())] > 1
        ):
            continue
        words = list(_WORDS.finditer(paragraph))
        for n in (3, 2):
            for index in range(len(words) - n + 1):
                group = words[index : index + n]
                anchor = paragraph[group[0].start() : group[-1].end()]
                terms = [m.group().casefold() for m in group]
                if (
                    len(set(terms) & shared) >= 2
                    and len(set(terms)) == len(terms)
                    and re.fullmatch(r"[A-Za-z0-9-]+(?: [A-Za-z0-9-]+){1,2}", anchor)
                    and anchor.casefold() not in used
                    and len(anchor) <= 80
                    and (selected_anchor is None or anchor == selected_anchor)
                ):
                    choices.append((start + group[0].start(), anchor))
    if not choices:
        raise TechnicalRecipeUnavailable("INTERNAL_LINK_ANCHOR_UNAVAILABLE")
    start, anchor = sorted(choices, key=lambda pair: (pair[0], -len(pair[1])))[0]
    replacement = '<a href="' + escape(target, quote=True) + '">' + anchor + "</a>"
    after = (source[:start] + replacement + source[start + len(anchor) :]).encode()
    try:
        plan_candidate_build(
            extension,
            checkout,
            patch={source_path: after},
            approved_paths=frozenset({source_path}),
        )
    except CandidatePolicyRejected:
        raise TechnicalRecipeUnavailable("RECIPE_PROTECTED_PATH") from None
    assert_internal_link_diff(
        before, after, start=len(source[:start].encode()), anchor=anchor, target=target
    )
    return RecipePatch(
        source_path,
        before,
        after,
        anchor,
        replacement,
        len(source[:start].encode()),
        "Add one relevant contextual link; incoming counts describe only the committed crawl.",
        "Revert only this exact link wrapper against the recorded base; preserve later edits.",
    )


def assert_internal_link_diff(before, after, *, start, anchor, target):
    old = anchor.encode()
    new = ('<a href="' + escape(target, quote=True) + '">' + anchor + "</a>").encode()
    if (
        before[start : start + len(old)] != old
        or after != before[:start] + new + before[start + len(old) :]
    ):
        raise TechnicalRecipeUnavailable("INTERNAL_LINK_EXACT_DIFF_FAILED")
