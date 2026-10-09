"""Independent, bounded verification of sealed static-page postconditions."""

import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import rfc8785
from psycopg import Connection, DatabaseError

from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_frontier import (
    CrawlFrontierUnavailable,
    CrawlRunOpened,
    claim_crawl_frontier,
)
from signal_core.crawl_http import EgressHttpRequest
from signal_core.crawl_urls import CrawlScopePolicy, normalize_crawl_url
from signal_core.egress_profiles import EgressProfile
from signal_core.shared_egress import (
    SharedEgressBlocked,
    SharedEgressConflict,
    SharedEgressDeferred,
    SharedEgressPending,
    SharedEgressPermitExpired,
    SharedEgressReceipt,
    SharedEgressRequest,
    SharedEgressUnavailable,
    execute_shared_egress,
)
from signal_core.structured_data_recipe import RECIPE_KEY, static_page_url
from signal_core.technical_seo_recipes import RECIPE_FINDING_KEYS, TechnicalRecipeUnavailable


class LiveVerificationUnavailable(Exception):
    """The exact page, sealed contract, or crawl-only egress is unavailable."""


class _Html(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.titles: list[str] = []
        self.scripts: list[str] = []
        self._title: list[str] | None = None
        self._script: list[str] | None = None
        self.ambiguous = False
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        if len(attrs) != len(dict(attrs)):
            self.ambiguous = True
        self.tags.append((tag, dict(attrs)))
        if tag == "title":
            if self._title is not None:
                self.ambiguous = True
            self._title = []
        if tag == "script" and (dict(attrs).get("type") or "").lower() == "application/ld+json":
            self._script = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title" and self._title is not None:
            self.titles.append("".join(self._title))
            self._title = None
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None

    def handle_data(self, data):
        if self._title is not None:
            self._title.append(data)
        if self._script is not None:
            self._script.append(data)


class _ArticleHtml(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tokens = []
        self.hidden = []
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = (
            tag in {"head", "script", "style", "template", "noscript"}
            or "hidden" in attrs
            or attrs.get("aria-hidden") == "true"
            or any(
                v in (attrs.get("style") or "").replace(" ", "").lower()
                for v in ("display:none", "visibility:hidden")
            )
        )
        if self.hidden or hidden:
            if tag not in {"meta", "link", "img", "br", "input", "hr"}:
                self.hidden.append(tag)
        elif tag in {"p", "h2", "a"}:
            self.tokens.append(("start", tag, attrs.get("href") if tag == "a" else None))

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in {"p", "h2", "a"}:
            self.tokens.append(("end", tag))

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.tokens.append(("text", " ".join(data.split())))


def _article_visible(fragment, text):
    expected, observed = _ArticleHtml(fragment).tokens, _ArticleHtml(text).tokens
    if not expected or len(observed) > 16000:
        return False
    return any(
        observed[i : i + len(expected)] == expected
        for i in range(len(observed) - len(expected) + 1)
    )


@dataclass(frozen=True)
class LivePostcondition:
    recipe_key: str
    page_url: str
    source_sha256: str
    result_sha256: str
    expected: dict
    recovery_plan: str


@dataclass(frozen=True)
class LiveVerification:
    outcome: str
    reason: str
    fetched_sha256: str | None
    http_status: int | None
    observed: dict
    postconditions: tuple[dict, ...]
    matched: bool

    def document(self) -> dict:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "fetched_sha256": self.fetched_sha256,
            "http_status": self.http_status,
            "observed": self.observed,
            "postconditions": list(self.postconditions),
            "matched": self.matched,
        }


def sealed_live_postcondition(
    manifest_bytes: bytes, revision_sha256: str, *, site_origin: str | None = None
) -> LivePostcondition:
    try:
        manifest = json.loads(manifest_bytes)
        if (
            rfc8785.dumps(manifest) != manifest_bytes
            or hashlib.sha256(manifest_bytes).hexdigest() != revision_sha256
            or manifest["schema_version"] != 1
        ):
            raise ValueError
        if manifest.get("work_type") in {"new_article", "content_refresh"}:
            if (
                manifest.get("approval_class") != "A2"
                or manifest.get("autonomy_eligible") is not False
                or len(manifest["changed_files"]) != 1
                or not isinstance(site_origin, str)
            ):
                raise ValueError
            origin = urlsplit(site_origin)
            change = manifest["changed_files"][0]
            path = change["path"]
            if (
                origin.scheme != "https"
                or origin.netloc != origin.hostname
                or origin.path
                or origin.query
                or origin.fragment
                or not isinstance(path, str)
                or re.fullmatch(r"[A-Za-z0-9_/-]+[.]html", path) is None
                or path.startswith("/")
                or any(p in {"", ".", ".."} for p in path.split("/"))
                or hashlib.sha256(change["after"].encode()).hexdigest() != change["result_sha256"]
                or not any(
                    a[0] == "_site/" + path and a[1] == change["result_sha256"]
                    for a in manifest["build_receipt"]["artifacts"]
                )
            ):
                raise ValueError
            page_url = site_origin + "/" + ("" if path == "index.html" else path)
            return LivePostcondition(
                manifest["work_type"],
                page_url,
                change["source_sha256"],
                change["result_sha256"],
                {"content_sha256": change["result_sha256"]},
                manifest["recovery_plan"],
            )
        finding = manifest["evidence"]["finding"]["key"]
        recipes = [key for key, keys in RECIPE_FINDING_KEYS.items() if finding in keys]
        structured = manifest.get("structured_data")
        internal_link = manifest.get("internal_link")
        if internal_link is not None:
            if (
                not isinstance(internal_link, dict)
                or internal_link.get("recipe_key") != "technical_internal_link_add"
                or internal_link.get("autonomy_eligible") is not False
            ):
                raise ValueError
            recipe = "technical_internal_link_add"
        elif structured is not None:
            if (
                not isinstance(structured, dict)
                or structured.get("recipe_key") != RECIPE_KEY
                or structured.get("autonomy_eligible") is not False
            ):
                raise ValueError
            recipe = RECIPE_KEY
        else:
            if len(recipes) != 1 or manifest["source_path"] != "index.html":
                raise ValueError
            recipe = recipes[0]
        page_url = manifest["evidence"]["page_url"]
        expected_url = static_page_url(manifest["source_path"], manifest["evidence"]["site_origin"])
        if page_url != expected_url or not isinstance(page_url, str):
            raise ValueError
        if any(
            not isinstance(manifest[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest[key]) is None
            for key in ("source_sha256", "result_sha256")
        ):
            raise ValueError
        fragment = manifest["patch"]["after"]
        before = manifest["patch"]["before"]
        if not isinstance(fragment, str) or not isinstance(before, str):
            raise ValueError
        if recipe == "technical_internal_link_add":
            target = internal_link["target_url"]
            from html import escape

            from signal_core.structured_data_recipe import site_url

            site_url(target, manifest["evidence"]["site_origin"])
            if fragment != '<a href="' + escape(target, quote=True) + '">' + before + "</a>":
                raise ValueError
            expected = {"content_sha256": manifest["result_sha256"]}
        elif recipe == RECIPE_KEY:
            parsed = _Html(fragment)
            if len(parsed.scripts) != 1 or json.loads(parsed.scripts[0]) != structured["json_ld"]:
                raise ValueError
            expected = {"json_ld": structured["json_ld"], "json_ld_bytes": parsed.scripts[0]}
        elif recipe == "technical_title":
            parsed = _Html(fragment if "<title>" in fragment else "<title>" + fragment + "</title>")
            if len(parsed.titles) != 1:
                raise ValueError
            expected = {"title": parsed.titles[0]}
        elif recipe == "technical_structured_data":
            expected = {"json_ld": json.loads(fragment)}
        else:
            parsed = _Html(fragment)
            if len(parsed.tags) != 1 or parsed.ambiguous:
                raise ValueError
            tag, attrs = parsed.tags[0]
            if recipe == "technical_description" and tag == "meta":
                expected = {"meta_description": attrs["content"]}
            elif recipe == "technical_canonical" and tag == "link":
                expected = {"canonical": attrs["href"]}
            elif recipe == "technical_alt" and tag == "img":
                expected = {"image_src": attrs["src"], "image_alt": attrs["alt"]}
            elif recipe == "technical_broken_link" and tag == "a" and "href" not in attrs:
                prior = _Html(before)
                expected = {"removed_link": urljoin(page_url, prior.tags[0][1]["href"])}
            else:
                raise ValueError
        maximum = 8192 if recipe == RECIPE_KEY else 2048
        if not manifest["recovery_plan"] or len(rfc8785.dumps(expected)) > maximum:
            raise ValueError
        return LivePostcondition(
            recipe,
            page_url,
            manifest["source_sha256"],
            manifest["result_sha256"],
            expected,
            manifest["recovery_plan"],
        )
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError, TechnicalRecipeUnavailable):
        raise LiveVerificationUnavailable("SEALED_POSTCONDITION_UNAVAILABLE") from None


def verify_live_html(
    contract: LivePostcondition, *, body: bytes, http_status: int, final_url: str
) -> LiveVerification:
    digest = hashlib.sha256(body).hexdigest()
    if final_url != contract.page_url:
        return LiveVerification(
            "inconclusive", "EC_123_URL_MISMATCH", digest, http_status, {}, (), False
        )
    if http_status != 200:
        outcome = "regressed" if http_status in {404, 410} or http_status >= 500 else "inconclusive"
        return LiveVerification(outcome, "LIVE_HTTP_REJECTED", digest, http_status, {}, (), False)
    try:
        if len(body) > 128 * 1024 or b"\x00" in body:
            raise ValueError
        parsed = _Html(body.decode("utf-8"))
        descriptions = [
            a.get("content")
            for t, a in parsed.tags
            if t == "meta" and (a.get("name") or "").casefold() == "description"
        ]
        canonicals = [
            urljoin(final_url, a["href"])
            for t, a in parsed.tags
            if t == "link"
            and "canonical" in (a.get("rel") or "").casefold().split()
            and a.get("href")
        ]
        robots = [
            a.get("content", "") or ""
            for t, a in parsed.tags
            if t == "meta" and (a.get("name") or "").casefold() in {"robots", "googlebot"}
        ]
        if len(parsed.tags) > 4096 or parsed.ambiguous:
            raise ValueError
        observed = {
            "title": parsed.titles,
            "meta_description": descriptions,
            "canonical": canonicals,
            "robots": robots,
        }
        checks = []
        for field, value in contract.expected.items():
            if field == "content_sha256":
                actual = digest
                match = digest == value
            elif field == "title":
                actual = parsed.titles
                match = actual == [value]
            elif field == "meta_description":
                actual = descriptions
                match = actual == [value]
            elif field == "canonical":
                actual = canonicals
                match = actual == [value]
            elif field == "image_src":
                continue
            elif field == "image_alt":
                actual = [
                    a.get("alt")
                    for t, a in parsed.tags
                    if t == "img" and a.get("src") == contract.expected["image_src"]
                ]
                match = actual == [value]
            elif field == "json_ld":
                actual = [json.loads(script) for script in parsed.scripts]
                match = actual == [value]
            elif field == "json_ld_bytes":
                actual = parsed.scripts
                match = actual == [value]
            elif field == "article_fragment":
                # REST already matched the sealed raw content; public GET must expose it too.
                actual = hashlib.sha256(value.encode()).hexdigest()
                match = _article_visible(value, body.decode("utf-8"))
                value = actual
            elif field == "removed_link":
                actual = [
                    urljoin(final_url, a["href"])
                    for t, a in parsed.tags
                    if t == "a" and a.get("href")
                ]
                match = value not in actual
                actual = [link for link in actual if link == value]
            else:
                raise ValueError
            checks.append({"field": field, "expected": value, "observed": actual, "matched": match})
        maximum = 16384 if contract.recipe_key == RECIPE_KEY else 8192
        if len(rfc8785.dumps(observed)) > 8192 or len(rfc8785.dumps(checks)) > maximum:
            raise ValueError
        matched = all(check["matched"] for check in checks) and bool(checks)
        if digest == contract.source_sha256:
            outcome, reason = "inconclusive", "EC_077_STALE_PAGE"
        elif any("noindex" in value.casefold().replace(",", " ").split() for value in robots):
            outcome, reason = "regressed", "LIVE_NOINDEX"
        elif canonicals and canonicals != [contract.page_url]:
            outcome, reason = "regressed", "LIVE_CANONICAL_DRIFT"
        elif matched and contract.recipe_key == "wordpress_article":
            outcome, reason = "verified", "SEALED_ARTICLE_VISIBLE"
        elif digest == contract.result_sha256 and matched:
            outcome, reason = "verified", "EXACT_SEALED_RESULT"
        else:
            outcome, reason = (
                "inconclusive",
                "EC_123_POSTCONDITION_MISMATCH" if not matched else "EC_123_PAGE_CHANGED",
            )
        return LiveVerification(
            outcome, reason, digest, http_status, observed, tuple(checks), matched
        )
    except (ValueError, UnicodeError, TypeError, KeyError):
        return LiveVerification(
            "inconclusive", "LIVE_HTML_UNAVAILABLE", digest, http_status, {}, (), False
        )


@dataclass(frozen=True)
class SharedLiveVerifier:
    admission_connection: Connection
    ingest_connection: Connection
    store: EncryptedLocalArtifactStore
    run: CrawlRunOpened
    policy: CrawlScopePolicy
    fetcher: object
    worker_key: str
    admission_policy: OriginAdmissionPolicy
    artifact_key: ArtifactEncryptionKey | None = None

    def verify(
        self, contract: LivePostcondition, *, site_id: UUID, operation_id: UUID
    ) -> tuple[LiveVerification, UUID | None]:
        if (
            self.run.site_id != site_id
            or self.policy.allowed_origins != (normalize_crawl_url(contract.page_url).origin,)
            or self.policy.max_redirects != 0
            or self.policy.max_body_bytes > 128 * 1024
            or self.policy.user_agent != "SignalBot/1.0 (+https://signal.example/bot)"
            or self.policy.request_timeout_seconds > 10
        ):
            raise LiveVerificationUnavailable("LIVE_EGRESS_SCOPE_REJECTED")
        request = SharedEgressRequest(
            "crawl",
            EgressHttpRequest(
                "GET",
                contract.page_url,
                headers=(("accept", "text/html,application/xhtml+xml"),),
                accepted_media_types=("text/html", "application/xhtml+xml"),
                max_response_bytes=128 * 1024,
                timeout_seconds=10,
            ),
            profile=EgressProfile.CRAWL_PAGE,
        )
        try:
            lease = claim_crawl_frontier(
                self.admission_connection,
                self.run,
                worker_key=self.worker_key,
                lease_id=operation_id,
                lease_seconds=30,
            )
            if lease is None or lease.url.fetch_url != contract.page_url:
                return LiveVerification(
                    "inconclusive", "LIVE_FRONTIER_UNAVAILABLE", None, None, {}, (), False
                ), None
            result = execute_shared_egress(
                self.admission_connection,
                self.ingest_connection,
                self.store,
                self.run,
                self.policy,
                self.fetcher,
                request,
                operation_id=operation_id,
                worker_key=self.worker_key,
                admission_policy=self.admission_policy,
                artifact_key=self.artifact_key,
            )
        except (
            SharedEgressUnavailable,
            SharedEgressConflict,
            SharedEgressPermitExpired,
            CrawlFrontierUnavailable,
            DatabaseError,
        ):
            return LiveVerification(
                "inconclusive", "LIVE_EGRESS_UNAVAILABLE", None, None, {}, (), False
            ), None
        if isinstance(result, SharedEgressBlocked):
            reason = "LIVE_ROBOTS_DENIED"
        elif isinstance(result, SharedEgressDeferred):
            reason = "LIVE_ORIGIN_BACKOFF"
        elif isinstance(result, SharedEgressPending):
            reason = "LIVE_OBSERVATION_UNKNOWN"
        elif isinstance(result, SharedEgressReceipt):
            if result.response.outcome == "fetched":
                return verify_live_html(
                    contract,
                    body=result.response.body,
                    http_status=result.response.http_status,
                    final_url=result.response.final_url,
                ), operation_id
            reason = (
                "LIVE_FETCH_TIMEOUT"
                if result.response.outcome == "transport_error"
                else "LIVE_FETCH_REJECTED"
            )
        else:
            raise LiveVerificationUnavailable("LIVE_RECEIPT_INVALID")
        return LiveVerification(
            "inconclusive", reason, None, None, {}, (), False
        ), operation_id if isinstance(result, (SharedEgressReceipt, SharedEgressPending)) else None
