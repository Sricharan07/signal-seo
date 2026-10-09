"""Evidence-bound technical findings from one immutable completed crawl."""

from collections import defaultdict
from dataclasses import dataclass, replace
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.database import _clean_transaction

DETECTOR_RELEASE_ID = UUID("0fc2a362-f51d-4b75-9bbf-22d456f29b40")
_NAMESPACE = UUID("1cb6aab8-277d-462c-8ec7-94937f076ade")
_MAX_FINDINGS = 10000


class CrawlAuditUnavailable(RuntimeError):
    """The exact completed manifest or its evidence is unavailable."""


class CrawlAuditConflict(RuntimeError):
    """An existing immutable report differs from this detector output."""


@dataclass(frozen=True)
class CrawlAuditInput:
    run_id: UUID
    manifest_sha256: bytes
    manifest_coverage: str
    discovered_count: int
    frontier_id: UUID
    url_id: UUID
    url: str
    depth: int
    discovered_from_url_id: UUID | None
    settlement_id: UUID
    terminal_state: str
    http_status: int | None
    robots_snapshot_id: UUID | None
    page_record_id: UUID | None
    title: str | None
    meta_description: str | None
    canonical_url: str | None
    headings: tuple[tuple[int, str], ...]
    structured_data_types: tuple[str, ...]
    internal_links: tuple[str, ...]
    parse_error_count: int
    output_truncated: bool
    body_sha256: bytes | None
    missing_alt_images: tuple[str, ...] = ()


@dataclass(frozen=True)
class CrawlAuditFinding:
    id: UUID
    key: str
    title: str
    summary: str
    severity: str
    resource_locator: str
    source_kind: str
    source_id: UUID

    def to_json(self) -> dict[str, str]:
        return {
            "id": str(self.id),
            "key": self.key,
            "title": self.title,
            "summary": self.summary,
            "severity": self.severity,
            "resource_locator": self.resource_locator,
            "source_kind": self.source_kind,
            "source_id": str(self.source_id),
        }


@dataclass(frozen=True)
class CrawlAuditReport:
    id: UUID
    manifest_id: UUID
    manifest_sha256: str
    detector_release_id: UUID
    coverage: dict[str, str]
    input_count: int
    findings: tuple[CrawlAuditFinding, ...]
    reused: bool = False


def detect_crawl_findings(manifest_id: UUID, rows: tuple[CrawlAuditInput, ...]) -> CrawlAuditReport:
    """Apply conservative rules only to immutable observations, never page instructions."""
    if not isinstance(manifest_id, UUID) or not rows:
        raise CrawlAuditUnavailable("A completed crawl manifest is required.")
    first = rows[0]
    if (
        first.discovered_count != len(rows)
        or first.manifest_coverage not in {"complete", "partial"}
        or len(first.manifest_sha256) != 32
        or len({row.frontier_id for row in rows}) != len(rows)
        or any(
            row.run_id != first.run_id
            or row.manifest_sha256 != first.manifest_sha256
            or row.manifest_coverage != first.manifest_coverage
            or row.discovered_count != first.discovered_count
            for row in rows
        )
    ):
        raise CrawlAuditUnavailable("Crawl evidence does not cover the exact manifest.")
    report_id = uuid5(_NAMESPACE, f"{manifest_id}:{DETECTOR_RELEASE_ID}")
    findings: list[CrawlAuditFinding] = []

    def add(
        row: CrawlAuditInput,
        key: str,
        title: str,
        summary: str,
        severity: str,
        *,
        settlement: bool = False,
    ) -> None:
        source = row.settlement_id if settlement else row.page_record_id
        if source is None:
            raise CrawlAuditUnavailable("A finding has no immutable source record.")
        findings.append(
            CrawlAuditFinding(
                uuid5(report_id, f"{source}:{key}"),
                key,
                title,
                summary,
                severity,
                row.url,
                "settlement" if settlement else "page",
                source,
            )
        )
        if len(findings) > _MAX_FINDINGS:
            raise CrawlAuditUnavailable("The bounded finding limit was exceeded.")

    title_groups: dict[str, list[CrawlAuditInput]] = defaultdict(list)
    description_groups: dict[str, list[CrawlAuditInput]] = defaultdict(list)
    for row in rows:
        if row.terminal_state == "fetched":
            if row.page_record_id is None or row.body_sha256 is None:
                raise CrawlAuditUnavailable("Fetched page evidence is incomplete.")
            if not row.title:
                add(
                    row,
                    "metadata.title.missing",
                    "Missing page title",
                    "The observed HTML page has no non-empty title.",
                    "high",
                )
            else:
                title_groups[row.title.casefold().strip()].append(row)
            if not row.meta_description:
                add(
                    row,
                    "metadata.meta_description.missing",
                    "Missing meta description",
                    "The observed HTML page has no non-empty meta description.",
                    "medium",
                )
            else:
                description_groups[row.meta_description.casefold().strip()].append(row)
            if row.missing_alt_images:
                add(
                    row,
                    "images.alt.missing",
                    "Image alt attribute missing",
                    "The observed HTML page contains an image without an alt attribute.",
                    "medium",
                )
            h1_count = sum(level == 1 for level, _ in row.headings)
            if h1_count == 0:
                add(
                    row,
                    "headings.h1.missing",
                    "Missing H1",
                    "The observed HTML page has no non-empty H1 heading.",
                    "medium",
                )
            elif h1_count > 1:
                add(
                    row,
                    "headings.h1.multiple",
                    "Multiple H1 headings",
                    "The observed HTML page has more than one H1 heading.",
                    "low",
                )
            if row.canonical_url is None:
                add(
                    row,
                    "canonical.missing",
                    "No canonical link",
                    "The observed HTML page has no canonical link element.",
                    "low",
                )
            elif _origin(row.canonical_url) != _origin(row.url):
                add(
                    row,
                    "canonical.external",
                    "External canonical target",
                    "The observed canonical points outside the verified origin; review intent.",
                    "low",
                )
            if row.parse_error_count > 0:
                add(
                    row,
                    "structured_data.invalid_json_ld",
                    "Invalid JSON-LD",
                    "The bounded parser could not decode at least one JSON-LD block.",
                    "medium",
                )
        elif row.terminal_state == "http_error" and row.http_status in {404, 410}:
            if row.discovered_from_url_id is not None:
                add(
                    row,
                    "links.internal.not_found",
                    "Broken internal link",
                    f"A discovered internal target returned HTTP {row.http_status}.",
                    "medium",
                    settlement=True,
                )
        elif row.terminal_state == "robots_denied" and row.depth == 0:
            add(
                row,
                "robots.homepage.denied",
                "Homepage blocked by robots",
                "Current robots evidence denied the crawl of the site homepage.",
                "high",
                settlement=True,
            )
    for group in title_groups.values():
        if len(group) > 1:
            for row in group:
                add(
                    row,
                    "metadata.title.duplicate",
                    "Duplicate page title",
                    "More than one observed page in this crawl has the same title.",
                    "medium",
                )
    for group in description_groups.values():
        if len(group) > 1:
            for row in group:
                add(
                    row,
                    "metadata.meta_description.duplicate",
                    "Duplicate meta description",
                    "More than one observed page in this crawl has the same meta description.",
                    "low",
                )

    coverage = {
        "pages": "complete" if first.manifest_coverage == "complete" else "partial",
        "metadata": (
            "not_assessed"
            if not any(row.page_record_id is not None for row in rows)
            else "partial"
            if any(row.output_truncated for row in rows if row.page_record_id)
            else "observed"
        ),
        "sitemaps": "not_assessed",
        "robots": "partial"
        if any(row.terminal_state == "robots_denied" for row in rows)
        else "observed",
    }
    return CrawlAuditReport(
        report_id,
        manifest_id,
        first.manifest_sha256.hex(),
        DETECTOR_RELEASE_ID,
        coverage,
        len(rows),
        tuple(sorted(findings, key=lambda item: (item.resource_locator, item.key))),
    )


def analyze_crawl_manifest(
    connection: Connection, *, tenant_id: UUID, site_id: UUID, manifest_id: UUID
) -> CrawlAuditReport:
    """Read only scoped crawl evidence, then atomically seal one deterministic report."""
    if not all(isinstance(value, UUID) for value in (tenant_id, site_id, manifest_id)):
        raise ValueError("An exact tenant, site, and manifest are required.")
    with _clean_transaction(connection):
        raw = connection.execute(
            "SELECT * FROM control.load_crawl_audit_inputs(%s, %s, %s)",
            (tenant_id, site_id, manifest_id),
        ).fetchall()
    if not raw or len(raw) > 1000:
        raise CrawlAuditUnavailable("Completed crawl evidence is unavailable.")
    rows = tuple(_decode_row(row) for row in raw)
    with _clean_transaction(connection):
        image_rows = connection.execute(
            "SELECT * FROM control.load_crawl_image_evidence(%s, %s, %s)",
            (tenant_id, site_id, rows[0].run_id),
        ).fetchall()
    images = {}
    for page_id, gaps in image_rows:
        if (
            page_id in images
            or not isinstance(gaps, list)
            or len(gaps) > 128
            or any(not isinstance(src, str) or not 1 <= len(src) <= 2048 for src in gaps)
        ):
            raise CrawlAuditUnavailable("Stored image evidence is invalid.")
        images[page_id] = tuple(gaps)
    if any(row.terminal_state == "fetched" and row.page_record_id not in images for row in rows):
        raise CrawlAuditUnavailable("Image evidence is incomplete for this detector release.")
    rows = tuple(
        replace(row, missing_alt_images=images.get(row.page_record_id, ())) for row in rows
    )
    report = detect_crawl_findings(manifest_id, rows)
    with _clean_transaction(connection):
        sealed = connection.execute(
            "SELECT * FROM control.record_crawl_audit_report(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                site_id,
                manifest_id,
                report.id,
                report.detector_release_id,
                bytes.fromhex(report.manifest_sha256),
                Jsonb([finding.to_json() for finding in report.findings]),
                Jsonb(report.coverage),
                report.input_count,
            ),
        ).fetchone()
    if sealed is None or sealed[2] != "recorded" or sealed[0] != report.id:
        if sealed is not None and sealed[2] == "report_conflict":
            raise CrawlAuditConflict("The sealed audit report differs from this detector output.")
        raise CrawlAuditUnavailable("Crawl audit report could not be sealed.")
    return CrawlAuditReport(
        report.id,
        report.manifest_id,
        report.manifest_sha256,
        report.detector_release_id,
        report.coverage,
        report.input_count,
        report.findings,
        sealed[1],
    )


def _decode_row(row: tuple[object, ...]) -> CrawlAuditInput:
    try:
        headings = row[17] or []
        structured = row[18] or []
        links = row[19] or []
        if (
            not isinstance(headings, list)
            or not isinstance(structured, list)
            or not isinstance(links, list)
        ):
            raise ValueError()
        return CrawlAuditInput(
            *row[:17],
            tuple((item["level"], item["text"]) for item in headings),
            tuple(structured),
            tuple(links),
            *row[20:],
        )
    except (IndexError, KeyError, TypeError, ValueError):
        raise CrawlAuditUnavailable("Stored crawl audit evidence is invalid.") from None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
