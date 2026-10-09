"""Deterministic local-fixture analysis and authenticated finding projection."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from uuid import UUID, uuid4

from psycopg import Connection

from signal_core.authorization import AuthorizationDenied, InvalidSession, validated_human_inputs
from signal_core.database import _clean_transaction

LOCAL_FIXTURE_HTML = (
    '<!doctype html><html lang="en"><head><title>Signal fixture product</title></head>'
    "<body><main><h1>Fixture product</h1></main></body></html>"
)
LOCAL_FIXTURE_SOURCE = "fixture:local-pilot/missing-meta-description/v1"
LOCAL_FIXTURE_RESOURCE = "/fixture/missing-meta-description"
_MAX_HTML_CHARACTERS = 64 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


class AuditNotReady(Exception):
    """No completed current-user audit exists for the selected site."""


class EvidenceConflict(Exception):
    """Committed evidence for the audit does not match the detector input."""


@dataclass(frozen=True)
class AuditFinding:
    id: UUID
    evidence_id: UUID
    command_id: UUID
    manifest_id: UUID
    finding_key: str
    title: str
    summary: str
    resource_locator: str
    severity: str
    status: str
    confidence_class: str
    source_kind: str
    source_identifier: str
    content_sha256: str
    evidence_observed_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    reused: bool = False


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_description = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        values = {name.lower(): value for name, value in attrs}
        name = values.get("name")
        content = values.get("content")
        if (
            isinstance(name, str)
            and name.strip().lower() == "description"
            and isinstance(content, str)
            and bool(content.strip())
        ):
            self.has_description = True


def has_nonempty_meta_description(html: object) -> bool:
    """Return deterministic metadata presence for one bounded HTML document."""
    if not isinstance(html, str) or not html or len(html) > _MAX_HTML_CHARACTERS or "\x00" in html:
        raise ValueError("HTML evidence must be a non-empty bounded text document.")
    parser = _MetadataParser()
    parser.feed(html)
    parser.close()
    return parser.has_description


def record_authenticated_local_fixture_finding(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
) -> AuditFinding:
    """Analyze the fixed fixture and attach its finding to the latest completed audit."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if has_nonempty_meta_description(LOCAL_FIXTURE_HTML):
        raise RuntimeError("The local detector fixture no longer contains its expected finding.")
    content_hash = hashlib.sha256(LOCAL_FIXTURE_HTML.encode("utf-8")).digest()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.record_authenticated_local_fixture_finding("
            "%s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                uuid4(),
                uuid4(),
                content_hash,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Fixture finding projection failed.")
        outcome = row[18]
        if outcome == "invalid_session":
            raise InvalidSession()
        if outcome == "authorization_denied":
            raise AuthorizationDenied()
        if outcome == "audit_not_ready":
            raise AuditNotReady()
        if outcome == "evidence_conflict":
            raise EvidenceConflict()
        if outcome != "recorded":
            raise RuntimeError("Fixture finding projection failed.")
        finding = _finding_from_row(row)
        if finding.content_sha256 != content_hash.hex():
            raise RuntimeError("Fixture finding projection failed.")
        return finding


def read_authenticated_findings(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
) -> tuple[AuditFinding, ...]:
    """Read the selected site's current findings after live authority revalidation."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT * FROM control.read_authenticated_findings(%s, %s, %s)",
            (token_hash, requested_site_id, current_recovery_generation),
        ).fetchall()
        if not rows:
            raise RuntimeError("Finding read failed.")
        outcome = rows[0][18]
        if outcome == "invalid_session":
            raise InvalidSession()
        if outcome == "authorization_denied":
            raise AuthorizationDenied()
        if outcome == "not_found":
            return ()
        if any(row[18] != "found" for row in rows):
            raise RuntimeError("Finding read failed.")
        findings = tuple(_finding_from_row(row) for row in rows)
        if len(findings) > 50:
            raise RuntimeError("Finding read failed.")
        return findings


def _finding_from_row(row: object) -> AuditFinding:
    try:
        finding = AuditFinding(
            id=row[0],
            evidence_id=row[1],
            command_id=row[2],
            manifest_id=row[3],
            finding_key=row[4],
            title=row[5],
            summary=row[6],
            resource_locator=row[7],
            severity=row[8],
            status=row[9],
            confidence_class=row[10],
            source_kind=row[11],
            source_identifier=row[12],
            content_sha256=row[13],
            evidence_observed_at=row[14],
            first_seen_at=row[15],
            last_seen_at=row[16],
            reused=row[17],
        )
    except (IndexError, TypeError):
        raise RuntimeError("Finding projection is invalid.") from None
    common_values_are_valid = (
        isinstance(finding.id, UUID)
        and isinstance(finding.evidence_id, UUID)
        and isinstance(finding.command_id, UUID)
        and isinstance(finding.manifest_id, UUID)
        and finding.finding_key == "metadata.meta_description.missing"
        and finding.title == "Missing meta description"
        and finding.severity == "medium"
        and finding.status == "open"
        and finding.confidence_class == "deterministic"
        and isinstance(finding.content_sha256, str)
        and _SHA256.fullmatch(finding.content_sha256) is not None
        and all(
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None
            for value in (
                finding.evidence_observed_at,
                finding.first_seen_at,
                finding.last_seen_at,
            )
        )
        and finding.last_seen_at >= finding.first_seen_at
        and isinstance(finding.reused, bool)
    )
    source_values_are_valid = (
        finding.source_kind == "synthetic_fixture"
        and finding.source_identifier == LOCAL_FIXTURE_SOURCE
        and finding.resource_locator == LOCAL_FIXTURE_RESOURCE
        and finding.summary
        == "The synthetic page fixture does not contain a non-empty meta description."
    ) or (
        finding.source_kind == "verified_origin"
        and isinstance(finding.source_identifier, str)
        and finding.source_identifier.startswith("https://")
        and finding.resource_locator == finding.source_identifier
        and finding.summary
        == "The verified homepage does not contain a non-empty meta description."
    )
    if not common_values_are_valid or not source_values_are_valid:
        raise RuntimeError("Finding projection is invalid.")
    return finding
