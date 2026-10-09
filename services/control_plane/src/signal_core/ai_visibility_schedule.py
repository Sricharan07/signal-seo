"""Owner-scoped Temporal re-observation, with immutable pre-I/O spend holds."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from temporalio import activity, workflow
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from signal_core.ai_visibility import (
        RESERVED_COST_MICROS,
        VisibilityObservation,
        _usage,
        parse_citations,
        record_visibility_observation,
    )
    from signal_core.assistant_credentials import PROVIDERS
    from signal_core.assistant_providers import (
        AssistantProviderError,
        record_assistant_evidence,
        request_assistant_search,
    )
    from signal_core.database import _clean_transaction
    from signal_core.recovery_authority import RecoveryGeneration
    from signal_core.session_issuance import validate_recovery_generation
    from signal_core.session_tokens import hash_session_token
    from signal_core.shared_egress import SharedEgressProvider
    from signal_core.weekly_schedule import upsert_or_pause_schedule


class VisibilityScheduleUnavailable(RuntimeError):
    pass


def read_visibility_schedule(connection, *, session_token, site_id, generation):
    with _clean_transaction(connection):
        value = connection.execute(
            "SELECT control.read_ai_visibility_schedule(%s,%s,%s)",
            (hash_session_token(session_token), site_id, validate_recovery_generation(generation)),
        ).fetchone()[0]
    if value is None:
        raise VisibilityScheduleUnavailable("owner_access_denied")
    return value


def set_visibility_schedule(
    connection,
    *,
    session_token,
    site_id,
    generation,
    request_id,
    cadence_days=7,
    monthly_cap_micros=1_000_000,
    enabled=False,
):
    if (
        not isinstance(request_id, UUID)
        or type(cadence_days) is not int
        or not 1 <= cadence_days <= 30
        or type(monthly_cap_micros) is not int
        or not 0 <= monthly_cap_micros <= 100_000_000
        or type(enabled) is not bool
    ):
        raise ValueError("Invalid visibility schedule settings.")
    with _clean_transaction(connection):
        state = connection.execute(
            "SELECT control.set_ai_visibility_schedule(%s,%s,%s,%s,%s,%s,%s)",
            (
                hash_session_token(session_token),
                site_id,
                validate_recovery_generation(generation),
                request_id,
                cadence_days,
                monthly_cap_micros,
                enabled,
            ),
        ).fetchone()[0]
    if state not in {"updated", "replayed"}:
        raise VisibilityScheduleUnavailable("owner_access_denied" if state == "denied" else state)
    return state


@dataclass(frozen=True)
class VisibilitySite:
    tenant_id: str
    site_id: str


@dataclass(frozen=True)
class VisibilityRun:
    site: VisibilitySite
    run_id: str


@dataclass(frozen=True)
class VisibilityScheduleReconciler:
    connection_factory: object
    temporal: object
    recovery_source: object
    task_queue: str = "signal.visibility.v1"

    async def reconcile(self, site: VisibilitySite) -> str:
        generation = await self.recovery_source.current_generation()
        if not isinstance(generation, RecoveryGeneration):
            raise VisibilityScheduleUnavailable("RECOVERY_UNAVAILABLE")
        with self.connection_factory() as connection, _clean_transaction(connection):
            current = connection.execute(
                "SELECT control.ai_visibility_schedule_current(%s,%s,%s)",
                (UUID(site.tenant_id), UUID(site.site_id), generation.value),
            ).fetchone()[0]
        schedule_id = f"signal:visibility:{site.tenant_id}:{site.site_id}"
        if current is None:
            return await upsert_or_pause_schedule(
                self.temporal,
                schedule_id,
                None,
                pause_note="Owner settings, recovery authority or verified site unavailable",
                paused_outcome="unavailable",
            )
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                "AiVisibilityReobserve",
                site,
                id=f"signal:visibility-run:{site.tenant_id}:{site.site_id}",
                task_queue=self.task_queue,
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=timedelta(days=current["cadence_days"]))],
                time_zone_name="UTC",
            ),
            policy=SchedulePolicy(
                overlap=ScheduleOverlapPolicy.SKIP,
                catchup_window=timedelta(minutes=1),
                pause_on_failure=True,
            ),
        )
        return await upsert_or_pause_schedule(
            self.temporal,
            schedule_id,
            schedule,
            pause_note="Owner settings, recovery authority or verified site unavailable",
        )


class VisibilityActivities:
    """Factories are private worker ports, not API/model-controlled network authority."""

    def __init__(
        self,
        connection_factory,
        evidence_connection_factory,
        recovery_source,
        *,
        credentials=None,
        egress_factory=None,
        ceilings=None,
    ):
        self.connection_factory = connection_factory
        self.evidence_connection_factory = evidence_connection_factory
        self.recovery_source = recovery_source
        self.credentials = credentials
        self.egress_factory = egress_factory
        self.ceilings = dict(ceilings or {})
        if any(
            p not in PROVIDERS
            or type(n) is not int
            or not RESERVED_COST_MICROS[p] <= n <= 100_000_000
            for p, n in self.ceilings.items()
        ):
            raise ValueError("Reviewed integer provider ceilings are required.")

    def _query(self, function, args):
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT * FROM control." + function + "(" + ",".join(["%s"] * len(args)) + ")", args
            ).fetchone()

    @activity.defn(name="signal.visibility.reobserve.v1")
    async def run(self, request: VisibilityRun) -> dict:
        tenant, site, run = (
            UUID(request.site.tenant_id),
            UUID(request.site.site_id),
            UUID(request.run_id),
        )
        generation = await self.recovery_source.current_generation()
        if not isinstance(generation, RecoveryGeneration):
            raise VisibilityScheduleUnavailable("RECOVERY_UNAVAILABLE")
        packet = self._query("open_ai_visibility_run", (tenant, site, run, generation.value))[0]
        if packet.get("reason"):
            return {"state": "unavailable", "reason": packet["reason"]}
        statuses = []
        for question in packet["questions"]:
            question_id = UUID(question["id"])
            for provider in PROVIDERS:
                configured = (
                    self.credentials is not None
                    and self.egress_factory is not None
                    and provider in self.ceilings
                )
                operation, outcome = self._query(
                    "reserve_ai_visibility_call",
                    (
                        tenant,
                        site,
                        run,
                        question_id,
                        provider,
                        generation.value,
                        configured,
                        self.ceilings.get(provider, RESERVED_COST_MICROS[provider]),
                    ),
                )
                if outcome != "reserved":
                    statuses.append("outcome_unknown" if outcome == "outcome_unknown" else outcome)
                    continue
                # The reservation transaction is committed before either factory, secrets or egress.
                try:
                    current = await self.recovery_source.current_generation()
                    if current != generation:
                        raise VisibilityScheduleUnavailable("RECOVERY_CHANGED")

                    async def permit(operation=operation):
                        authority = await self.recovery_source.current_generation()
                        if not isinstance(authority, RecoveryGeneration) or authority != generation:
                            raise VisibilityScheduleUnavailable("RECOVERY_CHANGED")
                        if (
                            self._query(
                                "ai_visibility_call_permit",
                                (tenant, site, operation, authority.value),
                            )[0]
                            != "permitted"
                        ):
                            raise VisibilityScheduleUnavailable("AUTHORITY_UNAVAILABLE")

                    await permit()
                    egress = await self.egress_factory(request.site, operation)
                    if (
                        not isinstance(egress, SharedEgressProvider)
                        or egress.purpose != "model"
                        or egress.run.tenant_id != tenant
                        or egress.run.site_id != site
                    ):
                        raise VisibilityScheduleUnavailable("EGRESS_SCOPE_REJECTED")
                    result = await request_assistant_search(
                        self.credentials,
                        egress,
                        provider=provider,
                        question=question["question"],
                        operation_id=operation,
                        before_egress=permit,
                    )
                    parsed = parse_citations(provider, result.response, packet["site_origin"])
                    if parsed.status != "complete":
                        raise VisibilityScheduleUnavailable(parsed.failure_code)
                    with self.evidence_connection_factory() as evidence:
                        recorded = record_assistant_evidence(
                            evidence, tenant_id=tenant, site_id=site, result=result
                        )
                        observation = VisibilityObservation(
                            uuid4(),
                            provider,
                            result.model_reported,
                            datetime.now(UTC),
                            parsed.status,
                            recorded.evidence_id,
                            parsed.cited_pages,
                            parsed.other_domains,
                            _usage(provider, result.response),
                            self.ceilings[provider],
                            parsed.failure_code,
                        )
                        record_visibility_observation(
                            evidence,
                            tenant_id=tenant,
                            site_id=site,
                            question_id=question_id,
                            observation=observation,
                        )
                    status, reason, observed = parsed.status, parsed.failure_code, observation.id
                except (AssistantProviderError, VisibilityScheduleUnavailable) as error:
                    code = getattr(error, "code", None)
                    safe_codes = {
                        "ASSISTANT_PROVIDER_UNAVAILABLE",
                        "ASSISTANT_CREDENTIAL_REJECTED",
                        "ASSISTANT_RESPONSE_REJECTED",
                        "ASSISTANT_EVIDENCE_UNAVAILABLE",
                        "ASSISTANT_EGRESS_REQUIRED",
                        "ASSISTANT_PROVIDER_REJECTED",
                        "ASSISTANT_QUESTION_REJECTED",
                        "ASSISTANT_REQUEST_REJECTED",
                    }
                    status, reason, observed = (
                        "outcome_unknown",
                        code if code in safe_codes else "PROVIDER_OUTCOME_UNKNOWN",
                        None,
                    )
                except Exception:
                    # Private transport failures can contain credentials; retain only a fixed code.
                    status, reason, observed = "outcome_unknown", "PROVIDER_OUTCOME_UNKNOWN", None
                # Cancellation/process loss deliberately leaves no receipt and retains the hold.
                recorded = self._query(
                    "finish_ai_visibility_call", (tenant, site, operation, status, reason, observed)
                )[0]
                if recorded != "recorded":
                    raise VisibilityScheduleUnavailable("RECEIPT_CONFLICT")
                statuses.append(status)
        return {
            "state": "complete"
            if statuses and all(s == "complete" for s in statuses)
            else "unavailable",
            "wording": "observed change" if packet["observed_changes"] else "re-observation",
            "statuses": statuses,
        }


@workflow.defn(name="AiVisibilityReobserve")
class AiVisibilityReobserve:
    @workflow.run
    async def run(self, site: VisibilitySite) -> dict:
        run_id = str(
            uuid5(NAMESPACE_URL, "visibility-run:" + workflow.info().first_execution_run_id)
        )
        return await workflow.execute_activity(
            "signal.visibility.reobserve.v1",
            VisibilityRun(site, run_id),
            result_type=dict,
            start_to_close_timeout=timedelta(minutes=65),
            schedule_to_close_timeout=timedelta(minutes=70),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )


@dataclass(frozen=True)
class VisibilityRuntime:
    temporal: object
    site: VisibilitySite
    activities: VisibilityActivities
    reconciler: VisibilityScheduleReconciler

    def worker(self) -> Worker:
        return Worker(
            self.temporal,
            task_queue=self.reconciler.task_queue,
            workflows=[AiVisibilityReobserve],
            activities=[self.activities.run],
            max_concurrent_activities=1,
        )

    async def serve(self, stop: asyncio.Event, *, interval_seconds: int = 30):
        if type(interval_seconds) is not int or not 30 <= interval_seconds <= 300:
            raise ValueError("Bounded maintenance cadence is required.")
        async with self.worker():
            while not stop.is_set():
                await self.reconciler.reconcile(self.site)
                try:
                    await asyncio.wait_for(stop.wait(), interval_seconds)
                except TimeoutError:
                    pass
