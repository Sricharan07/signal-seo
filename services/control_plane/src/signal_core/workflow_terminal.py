"""Persist one terminal CrawlSite business projection under the workflow role."""

from uuid import uuid4

from psycopg import Connection

from signal_core.database import _clean_transaction
from signal_core.workflow_contracts import (
    CrawlManifestReference,
    CrawlTerminalProjection,
    validate_crawl_manifest_reference,
    validate_crawl_terminal_input,
    validate_crawl_terminal_projection,
)


class WorkflowTerminalUnavailable(Exception):
    """The exact running workflow projection is not currently available."""


class WorkflowTerminalConflict(Exception):
    """A different terminal result is already bound to the workflow."""


def record_crawl_terminal(
    connection: Connection,
    terminal: object,
) -> CrawlTerminalProjection:
    """Atomically record a terminal command event and workflow projection."""
    validated = validate_crawl_terminal_input(terminal)
    result = validated.result_reference
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT tenant_id, site_id, progress_event_id, command_id, workflow_id, "
            "first_run_id, command_status, workflow_state, result_reference, reason, "
            "duplicate, outcome FROM control.record_crawl_workflow_terminal("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                validated.tenant_id,
                validated.site_id,
                validated.command_id,
                validated.workflow_id,
                validated.first_run_id,
                validated.state,
                None if result is None else result.manifest_id,
                None if result is None else result.manifest_sha256,
                None if result is None else result.coverage,
                None if result is None else result.discovered_count,
                None if result is None else result.terminal_count,
                None if result is None else result.scope_version,
                None if result is None else result.crawl_policy_version,
                validated.reason,
                uuid4(),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Workflow terminal recording returned no outcome.")
    if row[11] == "workflow_unavailable":
        raise WorkflowTerminalUnavailable()
    if row[11] == "terminal_conflict":
        raise WorkflowTerminalConflict()
    if row[11] != "recorded":
        raise RuntimeError("Workflow terminal recording returned an invalid outcome.")

    result_reference = _result_reference(row[8])
    projection = CrawlTerminalProjection(
        schema_version=1,
        tenant_id=str(row[0]),
        site_id=str(row[1]),
        progress_event_id=str(row[2]),
        command_id=str(row[3]),
        workflow_id=row[4],
        first_run_id=row[5],
        command_status=row[6],
        workflow_state=row[7],
        result_reference=result_reference,
        reason=row[9],
        duplicate=row[10],
    )
    validate_crawl_terminal_projection(projection)
    if (
        projection.tenant_id != validated.tenant_id
        or projection.site_id != validated.site_id
        or projection.command_id != validated.command_id
        or projection.workflow_id != validated.workflow_id
        or projection.first_run_id != validated.first_run_id
        or projection.workflow_state != validated.state
        or projection.result_reference != validated.result_reference
        or projection.reason != validated.reason
    ):
        raise RuntimeError("Workflow terminal recording returned mismatched evidence.")
    return projection


def _result_reference(value: object) -> CrawlManifestReference | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "kind",
        "manifest_id",
        "manifest_sha256",
        "coverage",
        "discovered_count",
        "terminal_count",
        "scope_version",
        "crawl_policy_version",
    }:
        raise RuntimeError("Workflow terminal recording returned invalid result evidence.")
    if value["kind"] != "crawl_manifest":
        raise RuntimeError("Workflow terminal recording returned invalid result evidence.")
    result = CrawlManifestReference(
        schema_version=value["schema_version"],
        manifest_id=value["manifest_id"],
        manifest_sha256=value["manifest_sha256"],
        coverage=value["coverage"],
        discovered_count=value["discovered_count"],
        terminal_count=value["terminal_count"],
        scope_version=value["scope_version"],
        crawl_policy_version=value["crawl_policy_version"],
    )
    try:
        return validate_crawl_manifest_reference(result)
    except ValueError:
        raise RuntimeError(
            "Workflow terminal recording returned invalid result evidence."
        ) from None
