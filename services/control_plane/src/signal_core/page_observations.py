"""Authorized verified-homepage observation and immutable metadata evidence."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from html.parser import HTMLParser
from uuid import UUID, uuid4

from psycopg import Connection

from signal_core.audit_findings import AuditFinding
from signal_core.authorization import AuthorizationDenied, InvalidSession, validated_human_inputs
from signal_core.crawl_http import CrawlFetchRejected, CrawlFetchUnavailable, PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.database import _clean_transaction

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_HTML_CHARACTERS = 512 * 1024
_MAX_TITLE = 300
_MAX_HEADING = 500
_MAX_DESCRIPTION = 500
_USER_AGENT = "SignalBot/1.0 (+https://signal.example/bot)"


class OriginNotVerified(Exception):
    """The selected site has no current exact-origin owner proof."""


class PageObservationNotReady(Exception):
    """The selected site has no completed audit to bind the observation to."""


class PageObservationConflict(Exception):
    """A durable observation identity conflicts with different evidence."""


class PageObservationFailed(Exception):
    """The bounded external read completed with a known closed failure."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        super().__init__(outcome)


@dataclass(frozen=True)
class PreparedPageObservation:
    id: UUID
    site_id: UUID
    origin: str
    command_id: UUID
    manifest_id: UUID
    manifest_sha256: str
    verification_id: UUID
    prepared_at: datetime
    replayed: bool


@dataclass(frozen=True)
class ObservedPageMetadata:
    fetch_outcome: str
    http_status: int | None
    media_type: str | None
    final_url: str | None
    resolved_address: str | None
    body_sha256: str | None
    title: str | None
    heading: str | None
    meta_description: str | None
    elapsed_ms: int


@dataclass(frozen=True)
class PageObservation:
    intent_id: UUID
    evidence_id: UUID
    finding_id: UUID | None
    command_id: UUID
    manifest_id: UUID
    origin: str
    final_url: str
    http_status: int
    media_type: str
    title: str | None
    heading: str | None
    meta_description: str | None
    body_sha256: str
    observed_at: datetime
    reused: bool


@dataclass(frozen=True)
class PageAnalysis:
    observation: PageObservation
    finding: AuditFinding | None


class _PageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.meta_description: str | None = None
        self._title_depth = 0
        self._heading_depth = 0
        self._heading_complete = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._title_depth += 1
        elif lowered == "h1" and not self._heading_complete:
            self._heading_depth += 1
        elif lowered == "meta" and self.meta_description is None:
            values = {name.lower(): value for name, value in attrs}
            name = values.get("name")
            content = values.get("content")
            if isinstance(name, str) and name.strip().lower() == "description":
                self.meta_description = _clean_text(content, _MAX_DESCRIPTION)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title" and self._title_depth:
            self._title_depth -= 1
        elif lowered == "h1" and self._heading_depth:
            self._heading_depth -= 1
            if self._heading_depth == 0:
                self._heading_complete = True

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.title_parts.append(data)
        if self._heading_depth and not self._heading_complete:
            self.heading_parts.append(data)


def extract_page_metadata(html: object) -> tuple[str | None, str | None, str | None]:
    """Extract bounded title, first H1, and non-empty meta description."""
    if not isinstance(html, str) or not html or len(html) > _MAX_HTML_CHARACTERS or "\x00" in html:
        raise ValueError("HTML evidence must be a non-empty bounded text document.")
    parser = _PageMetadataParser()
    parser.feed(html)
    parser.close()
    return (
        _clean_text(" ".join(parser.title_parts), _MAX_TITLE),
        _clean_text(" ".join(parser.heading_parts), _MAX_HEADING),
        parser.meta_description,
    )


def prepare_authenticated_page_observation(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    idempotency_key: object,
) -> PreparedPageObservation:
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if not isinstance(idempotency_key, UUID):
        raise ValueError("A page-observation idempotency key is required.")
    request_hash = hashlib.sha256(
        f"page-observation-v1\0{requested_site_id}".encode("ascii")
    ).digest()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.prepare_authenticated_page_observation(%s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                uuid4(),
                idempotency_key,
                request_hash,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Page observation preparation failed.")
        outcome = row[8]
        if outcome == "invalid_session":
            raise InvalidSession()
        if outcome == "authorization_denied":
            raise AuthorizationDenied()
        if outcome == "origin_not_verified":
            raise OriginNotVerified()
        if outcome == "audit_not_ready":
            raise PageObservationNotReady()
        if outcome == "request_conflict":
            raise PageObservationConflict()
        if outcome != "prepared":
            raise RuntimeError("Page observation preparation failed.")
        return _prepared_from_row(row, requested_site_id)


def observe_verified_homepage(
    fetcher: PinnedHttpFetcher,
    prepared: PreparedPageObservation,
) -> ObservedPageMetadata:
    if not isinstance(fetcher, PinnedHttpFetcher) or not isinstance(
        prepared, PreparedPageObservation
    ):
        raise ValueError("A prepared page observation and pinned fetcher are required.")
    policy = CrawlScopePolicy(
        schema_version=1,
        allowed_origins=(prepared.origin,),
        user_agent=_USER_AGENT,
        max_redirects=3,
        max_body_bytes=512 * 1024,
        request_timeout_seconds=10.0,
        total_timeout_seconds=20.0,
    )
    try:
        result = fetcher.fetch(f"{prepared.origin}/", policy=policy)
    except CrawlFetchRejected:
        return _failed_observation("policy_rejected")
    except CrawlFetchUnavailable:
        return _failed_observation("transport_unavailable")
    if result.outcome != "fetched" or not 200 <= result.http_status <= 299:
        return ObservedPageMetadata(
            fetch_outcome="http_rejected",
            http_status=result.http_status,
            media_type=result.media_type,
            final_url=result.final_url,
            resolved_address=result.resolved_address,
            body_sha256=result.body_sha256,
            title=None,
            heading=None,
            meta_description=None,
            elapsed_ms=result.elapsed_ms,
        )
    try:
        html = _decode_html(result.body, result.response_headers)
        title, heading, meta_description = extract_page_metadata(html)
    except (LookupError, UnicodeError, ValueError):
        return ObservedPageMetadata(
            fetch_outcome="invalid_html",
            http_status=result.http_status,
            media_type=result.media_type,
            final_url=result.final_url,
            resolved_address=result.resolved_address,
            body_sha256=result.body_sha256,
            title=None,
            heading=None,
            meta_description=None,
            elapsed_ms=result.elapsed_ms,
        )
    return ObservedPageMetadata(
        fetch_outcome="observed",
        http_status=result.http_status,
        media_type=result.media_type,
        final_url=result.final_url,
        resolved_address=result.resolved_address,
        body_sha256=result.body_sha256,
        title=title,
        heading=heading,
        meta_description=meta_description,
        elapsed_ms=result.elapsed_ms,
    )


def record_authenticated_page_observation(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    prepared: PreparedPageObservation,
    observed: ObservedPageMetadata,
) -> PageObservation:
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if (
        not isinstance(prepared, PreparedPageObservation)
        or prepared.site_id != requested_site_id
        or not isinstance(observed, ObservedPageMetadata)
    ):
        raise ValueError("A matching prepared page observation is required.")
    if observed.body_sha256 is not None and _SHA256.fullmatch(observed.body_sha256) is None:
        raise ValueError("A valid page body digest is required.")
    body_hash = bytes.fromhex(observed.body_sha256) if observed.body_sha256 else None
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.record_authenticated_page_observation("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                prepared.id,
                uuid4(),
                uuid4(),
                observed.fetch_outcome,
                observed.http_status,
                observed.media_type,
                observed.final_url,
                observed.resolved_address,
                body_hash,
                observed.title,
                observed.heading,
                observed.meta_description,
                observed.elapsed_ms,
            ),
        ).fetchone()
    # Interpret known fetch failures only after the result transaction commits.
    # Raising inside the transaction would erase the durable failure evidence.
    return _recorded_observation_from_row(row, failure_outcome=observed.fetch_outcome)


def read_latest_authenticated_page_observation(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
) -> PageObservation | None:
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.read_latest_authenticated_page_observation(%s, %s, %s)",
            (token_hash, requested_site_id, current_recovery_generation),
        ).fetchone()
        if row is None:
            raise RuntimeError("Page observation read failed.")
        if row[16] == "not_found":
            return None
        return _recorded_observation_from_row(row)


def _recorded_observation_from_row(
    row: object,
    *,
    failure_outcome: str | None = None,
) -> PageObservation:
    if row is None:
        raise RuntimeError("Page observation projection failed.")
    outcome = row[16]
    if outcome == "invalid_session":
        raise InvalidSession()
    if outcome in {"authorization_denied", "observation_not_authorized"}:
        raise AuthorizationDenied()
    if outcome in {"observation_conflict", "evidence_conflict"}:
        raise PageObservationConflict()
    if outcome not in {"recorded", "found"}:
        raise RuntimeError("Page observation projection failed.")
    if row[14] != "observed":
        raise PageObservationFailed(failure_outcome or row[14])
    try:
        observation = PageObservation(
            intent_id=row[0],
            evidence_id=row[1],
            finding_id=row[2],
            command_id=row[3],
            manifest_id=row[4],
            origin=row[5],
            final_url=row[6],
            http_status=row[7],
            media_type=row[8],
            title=row[9],
            heading=row[10],
            meta_description=row[11],
            body_sha256=row[12],
            observed_at=row[13],
            reused=row[15],
        )
    except (IndexError, TypeError):
        raise RuntimeError("Page observation projection failed.") from None
    if not _valid_observation(observation):
        raise RuntimeError("Page observation projection failed.")
    return observation


def _prepared_from_row(row: object, site_id: UUID) -> PreparedPageObservation:
    try:
        prepared = PreparedPageObservation(
            id=row[0],
            site_id=site_id,
            origin=row[1],
            command_id=row[2],
            manifest_id=row[3],
            manifest_sha256=row[4],
            verification_id=row[5],
            prepared_at=row[6],
            replayed=row[7],
        )
    except (IndexError, TypeError):
        raise RuntimeError("Page observation preparation failed.") from None
    if not (
        all(
            isinstance(value, UUID)
            for value in (
                prepared.id,
                prepared.site_id,
                prepared.command_id,
                prepared.manifest_id,
                prepared.verification_id,
            )
        )
        and isinstance(prepared.origin, str)
        and prepared.origin.startswith("https://")
        and isinstance(prepared.manifest_sha256, str)
        and _SHA256.fullmatch(prepared.manifest_sha256) is not None
        and _aware(prepared.prepared_at)
        and isinstance(prepared.replayed, bool)
    ):
        raise RuntimeError("Page observation preparation failed.")
    return prepared


def _valid_observation(observation: PageObservation) -> bool:
    return (
        all(
            isinstance(value, UUID)
            for value in (
                observation.intent_id,
                observation.evidence_id,
                observation.command_id,
                observation.manifest_id,
            )
        )
        and (observation.finding_id is None or isinstance(observation.finding_id, UUID))
        and isinstance(observation.origin, str)
        and observation.origin.startswith("https://")
        and isinstance(observation.final_url, str)
        and (
            observation.final_url == observation.origin
            or observation.final_url.startswith(f"{observation.origin}/")
        )
        and isinstance(observation.http_status, int)
        and 200 <= observation.http_status <= 299
        and observation.media_type in {"text/html", "application/xhtml+xml"}
        and all(
            value is None or isinstance(value, str)
            for value in (
                observation.title,
                observation.heading,
                observation.meta_description,
            )
        )
        and isinstance(observation.body_sha256, str)
        and _SHA256.fullmatch(observation.body_sha256) is not None
        and _aware(observation.observed_at)
        and isinstance(observation.reused, bool)
    )


def _decode_html(body: bytes, headers: tuple[tuple[str, str], ...]) -> str:
    content_type = next((value for name, value in headers if name == "content-type"), "")
    message = Message()
    message["content-type"] = content_type
    charset = message.get_content_charset() or "utf-8"
    decoded = body.decode(charset, errors="strict")
    if len(decoded) > _MAX_HTML_CHARACTERS:
        raise ValueError("HTML evidence exceeds the parser bound.")
    return decoded


def _clean_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _failed_observation(outcome: str) -> ObservedPageMetadata:
    return ObservedPageMetadata(
        fetch_outcome=outcome,
        http_status=None,
        media_type=None,
        final_url=None,
        resolved_address=None,
        body_sha256=None,
        title=None,
        heading=None,
        meta_description=None,
        elapsed_ms=0,
    )


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )
