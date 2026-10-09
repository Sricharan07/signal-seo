"""Atomic acceptance of one harmless command; no dispatcher or provider calls."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.authorization import AuthorizationDenied, InvalidSession, validated_human_inputs
from signal_core.database import Scope, _clean_transaction, scoped_transaction
from signal_core.workflow_contracts import (
    CrawlManifestReference,
    validate_crawl_manifest_reference,
)


class IdempotencyConflict(Exception):
    """The key is bound to a different intent; details must not expose another site."""


class ScopeUnavailable(Exception):
    """Missing, inaccessible, or inactive scope; intentionally indistinguishable."""


class CommandNotFound(Exception):
    """The command is absent or not visible to the current human principal."""


@dataclass(frozen=True)
class AcceptedCommand:
    id: UUID
    reused: bool


@dataclass(frozen=True)
class AcceptedHumanCommand:
    id: UUID
    reused: bool
    status: str
    accepted_at: datetime


@dataclass(frozen=True)
class HumanCommandStatus:
    id: UUID
    actor_user_id: UUID
    kind: str
    status: str
    accepted_at: datetime
    workflow_id: str | None = None
    workflow_type: str | None = None
    first_run_id: str | None = None
    workflow_state: str | None = None
    projected_at: datetime | None = None
    result_reference: CrawlManifestReference | None = None
    terminal_reason: str | None = None


def accept_snapshot(
    connection: Connection, scope: Scope, *, actor_service: str, idempotency_key: str
) -> AcceptedCommand:
    if not isinstance(actor_service, str) or not re.fullmatch(
        r"[a-z][a-z0-9_.-]{0,63}", actor_service
    ):
        raise ValueError("Invalid internal service identity.")
    if not isinstance(idempotency_key, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", idempotency_key
    ):
        raise ValueError("Idempotency keys require 1-128 ASCII token characters.")
    intent = {"kind": "site.snapshot", "schema_version": 1, "site_id": str(scope.site_id)}
    fingerprint = hashlib.sha256(
        json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
    ).digest()
    principal = f"service:{actor_service}"
    with scoped_transaction(connection, scope):
        active = connection.execute(
            "SELECT 1 FROM app.sites s JOIN app.tenants t ON t.tenant_id = s.tenant_id "
            "WHERE s.tenant_id = %s AND s.id = %s "
            "AND s.state != 'archived' AND t.lifecycle = 'active'",
            (scope.tenant_id, scope.site_id),
        ).fetchone()
        if active is None:
            raise ScopeUnavailable()
        command_id, event_id = uuid4(), uuid4()
        payload = Jsonb({"schema_version": 1})
        inserted = connection.execute(
            "INSERT INTO app.commands (tenant_id, id, site_id, actor_service, "
            "kind, schema_version, "
            "principal_key, route_key, scope_kind, idempotency_key, request_fingerprint, payload) "
            "VALUES (%s, %s, %s, %s, 'site.snapshot', 1, %s, "
            "'internal.site.snapshot', 'site', %s, %s, %s) "
            "ON CONFLICT (tenant_id, principal_key, route_key, idempotency_key) "
            "DO NOTHING RETURNING id",
            (
                scope.tenant_id,
                command_id,
                scope.site_id,
                actor_service,
                principal,
                idempotency_key,
                fingerprint,
                payload,
            ),
        ).fetchone()
        if inserted is None:
            # A conflicting key in another site is invisible under RLS. Return no scope detail.
            existing = connection.execute(
                "SELECT id, request_fingerprint FROM app.commands WHERE tenant_id = %s "
                "AND principal_key = %s AND route_key = 'internal.site.snapshot' "
                "AND idempotency_key = %s",
                (scope.tenant_id, principal, idempotency_key),
            ).fetchone()
            if existing is None or existing[1] != fingerprint:
                raise IdempotencyConflict()
            return AcceptedCommand(existing[0], True)
        connection.execute(
            "INSERT INTO app.command_events (tenant_id, site_id, id, command_id, "
            "event_number, event_type, facts) "
            "VALUES (%s, %s, %s, %s, 1, 'command.accepted', %s)",
            (scope.tenant_id, scope.site_id, event_id, command_id, payload),
        )
        connection.execute(
            "INSERT INTO app.outbox (tenant_id, site_id, id, event_id, "
            "aggregate_kind, aggregate_id, event_type, schema_version, payload) "
            "VALUES (%s, %s, %s, %s, 'command', %s, 'command.accepted', 1, %s)",
            (scope.tenant_id, scope.site_id, uuid4(), event_id, command_id, payload),
        )
        return AcceptedCommand(command_id, False)


def accept_authenticated_snapshot(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    idempotency_key: object,
) -> AcceptedHumanCommand:
    """Atomically authorize and accept one harmless human-attributed command."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    _validate_idempotency_key(idempotency_key)
    command_id, event_id, outbox_id = uuid4(), uuid4(), uuid4()

    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT command_id, reused, status, accepted_at, outcome "
            "FROM control.accept_authenticated_snapshot(%s, %s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                idempotency_key,
                command_id,
                event_id,
                outbox_id,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Authenticated command acceptance failed.")
        if row[4] == "invalid_session":
            raise InvalidSession()
        if row[4] == "authorization_denied":
            raise AuthorizationDenied()
        if row[4] == "idempotency_conflict":
            raise IdempotencyConflict()
        if row[4] != "accepted":
            raise RuntimeError("Authenticated command acceptance failed.")
        accepted = AcceptedHumanCommand(
            id=row[0],
            reused=row[1],
            status=row[2],
            accepted_at=row[3],
        )
        _validate_accepted_human_command(accepted)
        return accepted


def read_authenticated_snapshot_command(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    command_id: object,
) -> HumanCommandStatus:
    """Read one current projection after rechecking the same live authority."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if not isinstance(command_id, UUID):
        raise CommandNotFound()

    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT command_id, actor_user_id, kind, status, accepted_at, "
            "workflow_id, workflow_type, first_run_id, workflow_state, projected_at, "
            "result_reference, terminal_reason, outcome "
            "FROM control.read_authenticated_snapshot_command(%s, %s, %s, %s)",
            (token_hash, requested_site_id, current_recovery_generation, command_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("Authenticated command read failed.")
        if row[12] == "invalid_session":
            raise InvalidSession()
        if row[12] == "authorization_denied":
            raise AuthorizationDenied()
        if row[12] == "not_found":
            raise CommandNotFound()
        if row[12] != "found":
            raise RuntimeError("Authenticated command read failed.")
        return _human_command_status(row)


def read_latest_authenticated_snapshot_command(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
) -> HumanCommandStatus:
    """Read the latest current-user snapshot projection for the selected site."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )

    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT command_id, actor_user_id, kind, status, accepted_at, "
            "workflow_id, workflow_type, first_run_id, workflow_state, projected_at, "
            "result_reference, terminal_reason, outcome "
            "FROM control.read_latest_authenticated_snapshot_command(%s, %s, %s)",
            (token_hash, requested_site_id, current_recovery_generation),
        ).fetchone()
        if row is None:
            raise RuntimeError("Authenticated command read failed.")
        if row[12] == "invalid_session":
            raise InvalidSession()
        if row[12] == "authorization_denied":
            raise AuthorizationDenied()
        if row[12] == "not_found":
            raise CommandNotFound()
        if row[12] != "found":
            raise RuntimeError("Authenticated command read failed.")
        return _human_command_status(row)


def _human_command_status(row: object) -> HumanCommandStatus:
    try:
        status = HumanCommandStatus(
            id=row[0],
            actor_user_id=row[1],
            kind=row[2],
            status=row[3],
            accepted_at=row[4],
            workflow_id=row[5],
            workflow_type=row[6],
            first_run_id=row[7],
            workflow_state=row[8],
            projected_at=row[9],
            result_reference=_crawl_result_reference(row[10]),
            terminal_reason=row[11],
        )
    except (IndexError, TypeError):
        raise RuntimeError("Authenticated command read failed.") from None
    _validate_human_command_status(status)
    return status


def _validate_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("Idempotency keys require 1-128 ASCII token characters.")
    return value


def _validate_accepted_human_command(value: AcceptedHumanCommand) -> None:
    if (
        not isinstance(value.id, UUID)
        or not isinstance(value.reused, bool)
        or value.status != "accepted"
        or not isinstance(value.accepted_at, datetime)
        or value.accepted_at.tzinfo is None
        or value.accepted_at.utcoffset() is None
    ):
        raise RuntimeError("Authenticated command acceptance failed.")


def _validate_human_command_status(value: HumanCommandStatus) -> None:
    common_invalid = (
        not isinstance(value.id, UUID)
        or not isinstance(value.actor_user_id, UUID)
        or value.kind != "site.snapshot"
        or not isinstance(value.accepted_at, datetime)
        or value.accepted_at.tzinfo is None
        or value.accepted_at.utcoffset() is None
    )
    if common_invalid:
        raise RuntimeError("Authenticated command read failed.")
    workflow_values = (
        value.workflow_id,
        value.workflow_type,
        value.first_run_id,
        value.workflow_state,
        value.projected_at,
    )
    terminal_values = (value.result_reference, value.terminal_reason)
    if (
        value.status == "accepted"
        and workflow_values == (None, None, None, None, None)
        and terminal_values == (None, None)
    ):
        return
    if (
        value.status == "workflow_admitted"
        and isinstance(value.workflow_id, str)
        and _WORKFLOW_ID.fullmatch(value.workflow_id) is not None
        and value.workflow_id.endswith(f":{value.id}")
        and value.workflow_type == "CrawlSite"
        and value.first_run_id is None
        and value.workflow_state == "admitted"
        and isinstance(value.projected_at, datetime)
        and value.projected_at.tzinfo is not None
        and value.projected_at.utcoffset() is not None
        and terminal_values == (None, None)
    ):
        return
    if (
        value.status == "processing"
        and isinstance(value.workflow_id, str)
        and _WORKFLOW_ID.fullmatch(value.workflow_id) is not None
        and value.workflow_id.endswith(f":{value.id}")
        and value.workflow_type == "CrawlSite"
        and isinstance(value.first_run_id, str)
        and _RUN_ID.fullmatch(value.first_run_id) is not None
        and value.workflow_state == "running"
        and isinstance(value.projected_at, datetime)
        and value.projected_at.tzinfo is not None
        and value.projected_at.utcoffset() is not None
        and terminal_values == (None, None)
    ):
        return
    if (
        value.status in {"succeeded", "failed", "cancelled"}
        and isinstance(value.workflow_id, str)
        and _WORKFLOW_ID.fullmatch(value.workflow_id) is not None
        and value.workflow_id.endswith(f":{value.id}")
        and value.workflow_type == "CrawlSite"
        and isinstance(value.first_run_id, str)
        and _RUN_ID.fullmatch(value.first_run_id) is not None
        and value.workflow_state == value.status
        and isinstance(value.projected_at, datetime)
        and value.projected_at.tzinfo is not None
        and value.projected_at.utcoffset() is not None
    ):
        if (
            value.status == "succeeded"
            and value.result_reference is not None
            and value.terminal_reason is None
        ):
            validate_crawl_manifest_reference(value.result_reference)
            return
        if (
            value.status == "failed"
            and value.result_reference is None
            and value.terminal_reason == "crawl_activity_failed"
        ):
            return
        if (
            value.status == "cancelled"
            and value.result_reference is None
            and value.terminal_reason == "crawl_cancelled"
        ):
            return
    raise RuntimeError("Authenticated command read failed.")


def _crawl_result_reference(value: object) -> CrawlManifestReference | None:
    if value is None:
        return None
    expected_keys = {
        "kind",
        "schema_version",
        "manifest_id",
        "manifest_sha256",
        "coverage",
        "discovered_count",
        "terminal_count",
        "scope_version",
        "crawl_policy_version",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("kind") != "crawl_manifest"
    ):
        raise RuntimeError("Authenticated command read failed.")
    try:
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
        return validate_crawl_manifest_reference(result)
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Authenticated command read failed.") from None


_WORKFLOW_ID = re.compile(
    r"signal:CrawlSite:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_RUN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
