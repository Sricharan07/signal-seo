"""Closed health observations; monitoring never changes execution authority."""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from signal_core.database import Scope, _clean_transaction
from signal_core.session_tokens import hash_session_token

State = Literal["ok", "warning", "critical", "unknown"]
CHECKS = {
    "temporal": "Check the private Temporal service and worker connectivity.",
    "weekly_schedule": "Inspect weekly schedule pause, recent runs, and worker progress.",
    "measurement_schedule": "Inspect due measurement workflows and imported data coverage.",
    "outbox_backlog": "Inspect the command consumer; do not replay external writes.",
    "outbox_age": "Inspect the oldest pending command and consumer readiness.",
    **{
        f"binding_{p}": "Review the connector and reconnect through Settings if required."
        for p in ("gsc", "bing", "ga4", "github", "slack", "telegram", "email")
    },
    "egress_pins": "Refresh screened pins through the existing private egress configuration.",
    "openbao": "Inspect the private OpenBao service; never bypass secret controls.",
    "disk": "Inspect disk and WAL growth; preserve evidence before reclaiming storage.",
    "database_size": "Inspect database growth and retention; do not delete operation evidence.",
    "write_intents": "Reconcile failed or quarantined writes under the existing recovery gate.",
    **{
        f"budget_{p}": "Review usage and the human-configured budget; do not bypass caps."
        for p in ("model", "dataforseo", "assistants")
    },
}
REASONS = frozenset(
    {
        "within_threshold",
        "approaching_threshold",
        "threshold_exceeded",
        "probe_unavailable",
        "not_configured",
        "revoked",
        "expired",
        "import_failing",
        "stale_evidence",
        "reachable",
        "unreachable",
        "sealed",
        "unsealed",
        "paused",
        "stuck",
        "healthy",
        "budget_exhausted",
        "pin_expired",
        "pin_expiring",
        "no_pin_evidence",
    }
)


@dataclass(frozen=True)
class CheckResult:
    check: str
    state: State
    reason: str
    checked_at: str
    remediation: str


def evaluate(check: str, observation: Mapping | None, now: datetime) -> CheckResult:
    if check not in CHECKS or now.tzinfo is None:
        raise ValueError("An exact check and aware clock are required.")
    state: State = "unknown"
    reason = "probe_unavailable"
    if isinstance(observation, Mapping):
        condition = observation.get("condition")
        states = {
            "reachable": "ok",
            "healthy": "ok",
            "unsealed": "ok",
            "unreachable": "critical",
            "sealed": "critical",
            "budget_exhausted": "critical",
            "stuck": "critical",
            "revoked": "critical",
            "expired": "critical",
            "import_failing": "warning",
            "paused": "warning",
            "not_configured": "unknown",
            "stale_evidence": "unknown",
        }
        if isinstance(condition, str) and condition in states:
            state, reason = states[condition], condition
        elif check == "egress_pins":
            expires = observation.get("expires_at")
            if isinstance(expires, datetime) and expires.tzinfo is not None:
                remaining = (expires - now).total_seconds()
                state, reason = (
                    ("critical", "pin_expired")
                    if remaining <= 0
                    else (
                        ("warning", "pin_expiring")
                        if remaining <= 300
                        else ("ok", "within_threshold")
                    )
                )
            else:
                reason = "no_pin_evidence"
        else:
            value, warning, critical = (
                observation.get(k) for k in ("value", "warning", "critical")
            )
            if (
                all(
                    type(v) in (int, float) and 0 <= v <= 1e18 and math.isfinite(v)
                    for v in (value, warning, critical)
                )
                and warning < critical
            ):
                if value >= critical:
                    state, reason = (
                        "critical",
                        (
                            "budget_exhausted"
                            if check.startswith("budget_")
                            else "threshold_exceeded"
                        ),
                    )
                elif value >= warning:
                    state, reason = "warning", "approaching_threshold"
                else:
                    state, reason = "ok", "within_threshold"
    return CheckResult(check, state, reason, now.astimezone(UTC).isoformat(), CHECKS[check])


@dataclass(frozen=True, repr=False)
class HealthStore:
    connection_factory: Callable

    def facts(self, scope: Scope) -> dict:
        with self.connection_factory() as connection, _clean_transaction(connection):
            result = connection.execute(
                "SELECT control.health_facts(%s,%s)", (scope.tenant_id, scope.site_id)
            ).fetchone()[0]
        if result is None:
            raise PermissionError("HEALTH_SCOPE_DENIED")
        return result

    def record(self, scope: Scope, results: tuple[CheckResult, ...], generation: str) -> dict:
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT control.record_health_checks(%s,%s,%s,%s)",
                (scope.tenant_id, scope.site_id, generation, Jsonb([asdict(r) for r in results])),
            ).fetchone()[0]

    def read(self, token: str, site: UUID, generation: str) -> dict:
        with self.connection_factory() as connection, _clean_transaction(connection):
            result = connection.execute(
                "SELECT control.read_health_checks(%s,%s,%s)",
                (hash_session_token(token), site, generation),
            ).fetchone()[0]
        if result is None:
            raise PermissionError("HEALTH_AUTHORITY_DENIED")
        return result


Probe = Callable[[], Awaitable[Mapping]]


@dataclass(repr=False)
class HealthMonitor:
    store: HealthStore
    scope: Scope
    recovery_source: object
    probes: Mapping[str, Probe] = field(default_factory=dict)
    # 0133 registers durable chat-outbox consumers here, never a raw provider sender.
    chat_alerts: Callable[[Scope, str], Awaitable[None]] | None = None
    latest: tuple[CheckResult, ...] = ()
    persisted: bool = False

    def __post_init__(self):
        if set(self.probes) - CHECKS.keys():
            raise ValueError("Unknown monitoring probe.")

    async def run_once(self) -> tuple[CheckResult, ...]:
        self.persisted = False
        now = datetime.now(UTC)
        try:
            facts = await asyncio.to_thread(self.store.facts, self.scope)
        except Exception:
            facts = {}
        results = []
        for check in CHECKS:
            observation = facts.get(check)
            if check in self.probes:
                try:
                    observation = await asyncio.wait_for(self.probes[check](), timeout=5)
                except Exception:
                    observation = None
            results.append(evaluate(check, observation, now))
        self.latest = tuple(results)
        for result in self.latest:
            if result.state == "critical":
                logging.getLogger(__name__).error(
                    "health_check check=%s state=%s reason=%s",
                    result.check,
                    result.state,
                    result.reason,
                )
        try:
            generation = (await self.recovery_source.current_generation()).value
            receipt = await asyncio.to_thread(
                self.store.record, self.scope, self.latest, generation
            )
            self.persisted = receipt is not None
            if receipt and receipt.get("alerts", 0):
                logging.getLogger(__name__).warning(
                    "health_alert_committed count=%d", receipt["alerts"]
                )
            if receipt is not None and self.chat_alerts is not None:
                await self.chat_alerts(self.scope, generation)
        except Exception:
            logging.getLogger(__name__).error("health_monitor_persistence_or_delivery_unavailable")
        return self.latest

    async def serve(self, stop: asyncio.Event, *, interval_seconds: int = 60) -> None:
        if type(interval_seconds) is not int or not 30 <= interval_seconds <= 300:
            raise ValueError("A bounded monitoring cadence is required.")
        while not stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass
