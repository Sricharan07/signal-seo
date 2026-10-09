"""Deterministic, serialization-safe contracts shared with Temporal workflows."""

import re
from dataclasses import dataclass
from uuid import UUID

CRAWL_ACTIVITY_NAME = "signal.crawl-site.execute.v1"
TERMINAL_ACTIVITY_NAME = "signal.crawl-site.record-terminal.v1"


@dataclass(frozen=True)
class CrawlSiteWorkflowInput:
    schema_version: int
    tenant_id: str
    site_id: str
    command_id: str
    source_event_id: str
    scope_version: int = 1
    crawl_policy_version: int = 1


@dataclass(frozen=True)
class CrawlManifestReference:
    schema_version: int
    manifest_id: str
    manifest_sha256: str
    coverage: str
    discovered_count: int
    terminal_count: int
    scope_version: int
    crawl_policy_version: int


@dataclass(frozen=True)
class CrawlHeartbeat:
    schema_version: int
    phase: str
    discovered_count: int
    terminal_count: int


@dataclass(frozen=True)
class CrawlTerminalInput:
    schema_version: int
    tenant_id: str
    site_id: str
    command_id: str
    workflow_id: str
    first_run_id: str
    state: str
    result_reference: CrawlManifestReference | None
    reason: str | None


@dataclass(frozen=True)
class CrawlTerminalProjection:
    schema_version: int
    tenant_id: str
    site_id: str
    progress_event_id: str
    command_id: str
    workflow_id: str
    first_run_id: str
    command_status: str
    workflow_state: str
    result_reference: CrawlManifestReference | None
    reason: str | None
    duplicate: bool


def validate_crawl_workflow_input(value: object) -> CrawlSiteWorkflowInput:
    if (
        not isinstance(value, CrawlSiteWorkflowInput)
        or value.schema_version != 1
        or not all(
            _canonical_uuid(identifier)
            for identifier in (
                value.tenant_id,
                value.site_id,
                value.command_id,
                value.source_event_id,
            )
        )
        or not _version(value.scope_version)
        or not _version(value.crawl_policy_version)
    ):
        raise ValueError("Invalid CrawlSite workflow input.")
    return value


def validate_crawl_manifest_reference(value: object) -> CrawlManifestReference:
    if (
        not isinstance(value, CrawlManifestReference)
        or value.schema_version != 1
        or not _canonical_uuid(value.manifest_id)
        or not isinstance(value.manifest_sha256, str)
        or _SHA256.fullmatch(value.manifest_sha256) is None
        or value.coverage not in {"complete", "partial"}
        or not _count(value.discovered_count)
        or not _count(value.terminal_count)
        or value.terminal_count > value.discovered_count
        or not _version(value.scope_version)
        or not _version(value.crawl_policy_version)
    ):
        raise ValueError("Invalid crawl manifest reference.")
    return value


def validate_crawl_heartbeat(value: object) -> CrawlHeartbeat:
    if (
        not isinstance(value, CrawlHeartbeat)
        or value.schema_version != 1
        or value.phase not in {"starting", "crawling", "finalizing", "completed"}
        or not _count(value.discovered_count)
        or not _count(value.terminal_count)
        or value.terminal_count > value.discovered_count
    ):
        raise ValueError("Invalid crawl heartbeat.")
    return value


def validate_crawl_terminal_input(value: object) -> CrawlTerminalInput:
    if (
        not isinstance(value, CrawlTerminalInput)
        or value.schema_version != 1
        or not all(
            _canonical_uuid(identifier)
            for identifier in (
                value.tenant_id,
                value.site_id,
                value.command_id,
                value.first_run_id,
            )
        )
        or not isinstance(value.workflow_id, str)
        or value.workflow_id != f"signal:CrawlSite:{value.tenant_id}:{value.command_id}"
        or value.state not in {"succeeded", "failed", "cancelled"}
    ):
        raise ValueError("Invalid crawl terminal input.")
    if value.state == "succeeded":
        validate_crawl_manifest_reference(value.result_reference)
        if value.reason is not None:
            raise ValueError("Invalid crawl terminal input.")
    elif (
        value.result_reference is not None
        or value.reason
        != {
            "failed": "crawl_activity_failed",
            "cancelled": "crawl_cancelled",
        }[value.state]
    ):
        raise ValueError("Invalid crawl terminal input.")
    return value


def validate_crawl_terminal_projection(value: object) -> CrawlTerminalProjection:
    if (
        not isinstance(value, CrawlTerminalProjection)
        or value.schema_version != 1
        or not all(
            _canonical_uuid(identifier)
            for identifier in (
                value.tenant_id,
                value.site_id,
                value.progress_event_id,
                value.command_id,
                value.first_run_id,
            )
        )
        or not isinstance(value.workflow_id, str)
        or value.workflow_id != f"signal:CrawlSite:{value.tenant_id}:{value.command_id}"
        or value.workflow_state not in {"succeeded", "failed", "cancelled"}
        or value.command_status != value.workflow_state
        or not isinstance(value.duplicate, bool)
    ):
        raise ValueError("Invalid crawl terminal projection.")
    if value.workflow_state == "succeeded":
        validate_crawl_manifest_reference(value.result_reference)
        if value.reason is not None:
            raise ValueError("Invalid crawl terminal projection.")
    elif (
        value.result_reference is not None
        or value.reason
        != {
            "failed": "crawl_activity_failed",
            "cancelled": "crawl_cancelled",
        }[value.workflow_state]
    ):
        raise ValueError("Invalid crawl terminal projection.")
    return value


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _version(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and 1 <= value <= 2147483647


def _count(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and 0 <= value <= 1000000


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
