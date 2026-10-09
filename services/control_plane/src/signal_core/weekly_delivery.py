"""Compose existing sealed recipes, exact authorization, PRs, and observations."""

import base64
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg import Connection

from signal_core.autonomy_gate import AutonomyCandidate
from signal_core.database import Scope, _clean_transaction
from signal_core.github_delivery_observation import (
    GitHubObservationUnavailable,
    observe_github_delivery,
    read_authenticated_github_delivery_observations,
)
from signal_core.github_pr_delivery import (
    GitHubPrDeliveryUnavailable,
    open_approved_github_pr,
    read_authenticated_github_pr_operations,
)
from signal_core.recipe_autonomy import AUTONOMY_RECIPES, read_recipe_autonomy
from signal_core.recipe_releases import RecipeReleaseUnavailable, get_reviewed_recipe_release
from signal_core.technical_recipe_service import seal_technical_recipe_revision
from signal_core.technical_seo_recipes import RECIPE_FINDING_KEYS, TechnicalRecipeUnavailable
from signal_core.weekly_delivery_authority import (
    WeeklyDeliveryConnection,
    WeeklyWorkload,
    stable_identity,
)
from signal_core.weekly_delivery_reconciliation import reconcile_weekly_operation
from signal_core.weekly_loop import WeeklyCycle, WeeklyStage, WeeklyStageResult


@dataclass(frozen=True)
class WeeklyDeliveryIO:
    """Already admitted, site-bound provider ports supplied by the trusted worker."""

    credential: object
    github_read_transport: object
    github_egress: object
    runner: object
    journal: object
    live_verifier: object
    environment: str
    trusted_deployment_actor_id: int
    model: object = None
    openbao_transport: object = None


def workload_handle(cycle_id: str, finding_id: UUID) -> str:
    # This is a scoped worker reference, not a human session credential.
    return (
        base64.urlsafe_b64encode(
            sha256(f"weekly-delivery:{cycle_id}:{finding_id}".encode("ascii")).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )


def _workload(cycle: WeeklyCycle, row: dict) -> WeeklyWorkload:
    return WeeklyWorkload(
        **{
            key: UUID(row[key])
            for key in (
                "id",
                "revision_id",
                "operation_id",
                "gate_decision_id",
                "finding_id",
                "report_id",
                "recipe_release_id",
                "extension_id",
                "revision_key",
                "build_key",
            )
        },
        recipe_key=row["recipe_key"],
        handle=workload_handle(row["cycle_id"], UUID(row["finding_id"])),
    )


class WeeklyTechnicalDelivery:
    """One narrowly scoped workload per finding; retries reload durable identities."""

    def __init__(
        self,
        workflow_connection: Callable[[], AbstractContextManager[Connection]],
        release_connection: Callable[[], AbstractContextManager[Connection]],
        recovery_source,
        io_factory: Callable[[WeeklyCycle, WeeklyWorkload], WeeklyDeliveryIO],
        *,
        candidate_cost_cents: int,
    ):
        if type(candidate_cost_cents) is not int or not 0 <= candidate_cost_cents <= 100_000_000:
            raise ValueError("A bounded candidate cost is required.")
        self.workflow_connection = workflow_connection
        self.release_connection = release_connection
        self.recovery_source = recovery_source
        self.io_factory = io_factory
        self.candidate_cost_cents = candidate_cost_cents
        self.prepare_detail = "CANDIDATE_PORT_UNAVAILABLE"
        self.prepare_failure_refs = ()

    def rows(self, cycle: WeeklyCycle) -> tuple[dict, ...]:
        with self.workflow_connection() as connection, _clean_transaction(connection):
            rows = connection.execute(
                "SELECT * FROM control.list_weekly_delivery_workloads(%s,%s,%s)",
                (UUID(cycle.site.tenant_id), UUID(cycle.site.site_id), UUID(cycle.cycle_id)),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def backlog(self, cycle: WeeklyCycle) -> tuple[dict, ...]:
        with self.workflow_connection() as connection, _clean_transaction(connection):
            rows = connection.execute(
                "SELECT * FROM control.list_weekly_delivery_backlog(%s,%s)",
                (UUID(cycle.site.tenant_id), UUID(cycle.site.site_id)),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def _candidate(self, cycle: WeeklyCycle, row: dict) -> AutonomyCandidate:
        manifest = row["manifest"]
        if manifest is None:
            raise ValueError("A committed sealed revision is required.")
        return AutonomyCandidate(
            scope=Scope(UUID(cycle.site.tenant_id), UUID(cycle.site.site_id)),
            decision_id=UUID(row["gate_decision_id"]),
            operation_id=UUID(row["operation_id"]),
            grant_id=UUID(cycle.site.grant_id),
            recipe_release_id=UUID(row["recipe_release_id"]),
            sealed_revision_sha256=bytes.fromhex(row["revision_sha256"]),
            work_type="metadata_pr",
            approval_class="A2",
            resource_path="/" + manifest["source_path"],
            cost_cents=self.candidate_cost_cents,
            summary="Technical finding: " + manifest["evidence"]["finding"]["key"],
            claims_grounded=manifest.get("claim_review_required") is False,
            always_ask=row["recipe_key"] not in AUTONOMY_RECIPES,
        )

    async def prepare(
        self, stage: WeeklyStage, due_revisions: tuple[str, ...]
    ) -> tuple[AutonomyCandidate, ...]:
        cycle = stage.cycle
        failures = []
        self.prepare_failure_refs = ()
        generation = (await self.recovery_source.current_generation()).value
        existing = self.rows(cycle)
        if due_revisions:
            for digest in due_revisions:
                source = next(
                    (row for row in self.backlog(cycle) if row["revision_sha256"] == digest), None
                )
                if source is None:
                    self.prepare_detail = "DEFERRED_REVISION_EVIDENCE_UNAVAILABLE"
                    return ()
                seed = f"{cycle.cycle_id}:{source['finding_id']}"
                handle = workload_handle(cycle.cycle_id, UUID(source["finding_id"]))
                with self.workflow_connection() as connection, _clean_transaction(connection):
                    outcome = connection.execute(
                        "SELECT control.requeue_weekly_delivery_revision("
                        + ",".join(["%s"] * 9)
                        + ")",
                        (
                            UUID(cycle.site.tenant_id),
                            UUID(cycle.site.site_id),
                            UUID(cycle.cycle_id),
                            generation,
                            bytes.fromhex(digest),
                            stable_identity(seed + ":workload"),
                            stable_identity(seed + ":operation"),
                            stable_identity(seed + ":gate"),
                            sha256(handle.encode("ascii")).digest(),
                        ),
                    ).fetchone()[0]
                if outcome != "requeued":
                    self.prepare_detail = "DEFERRED_EXACT_OWNER_REVIEW_REQUIRED"
                    return ()
            rows = self.rows(cycle)
            return tuple(
                self._candidate(
                    cycle, next(row for row in rows if row["revision_sha256"] == digest)
                )
                for digest in due_revisions
            )
        if existing and all(row["manifest"] is not None for row in existing):
            return tuple(self._candidate(cycle, row) for row in existing)
        with self.workflow_connection() as connection, _clean_transaction(connection):
            inputs = connection.execute(
                "SELECT control.list_weekly_delivery_inputs(%s,%s,%s,%s)",
                (
                    UUID(cycle.site.tenant_id),
                    UUID(cycle.site.site_id),
                    UUID(cycle.cycle_id),
                    generation,
                ),
            ).fetchone()[0]
        if inputs is None or "unavailable" in inputs:
            self.prepare_detail = (inputs or {}).get("unavailable", "AUTHORITY_CHANGED")
            return ()
        releases = {}
        with self.release_connection() as connection:
            for release_id in inputs["release_ids"]:
                for key in sorted(AUTONOMY_RECIPES):
                    try:
                        release = get_reviewed_recipe_release(
                            connection, recipe_key=key, release_id=UUID(release_id)
                        )
                    except RecipeReleaseUnavailable:
                        continue
                    if read_recipe_autonomy(connection, release_id=release.id):
                        releases.setdefault(key, release.id)
        for finding in sorted(inputs["findings"], key=lambda item: item["id"])[:32]:
            key = next(
                (key for key in sorted(releases) if finding["key"] in RECIPE_FINDING_KEYS[key]),
                None,
            )
            if key is None:
                continue
            seed = f"{cycle.cycle_id}:{finding['id']}"
            revision_key = stable_identity(seed + ":revision-key")
            job = WeeklyWorkload(
                stable_identity(seed + ":workload"),
                uuid5(NAMESPACE_URL, f"technical-recipe:{cycle.site.site_id}:{revision_key}"),
                stable_identity(seed + ":operation"),
                stable_identity(seed + ":gate"),
                UUID(finding["id"]),
                UUID(inputs["report_id"]),
                releases[key],
                UUID(inputs["extension_id"]),
                key,
                revision_key,
                stable_identity(seed + ":build"),
                workload_handle(cycle.cycle_id, UUID(finding["id"])),
            )
            with self.workflow_connection() as connection, _clean_transaction(connection):
                opened = connection.execute(
                    "SELECT control.open_weekly_delivery_workload(" + ",".join(["%s"] * 15) + ")",
                    (
                        UUID(cycle.site.tenant_id),
                        UUID(cycle.site.site_id),
                        UUID(cycle.cycle_id),
                        generation,
                        job.id,
                        job.report_id,
                        job.finding_id,
                        job.recipe_release_id,
                        job.extension_id,
                        job.revision_id,
                        job.operation_id,
                        job.gate_decision_id,
                        job.revision_key,
                        job.build_key,
                        sha256(job.handle.encode("ascii")).digest(),
                    ),
                ).fetchone()[0]
            if opened != "opened":
                self.prepare_detail = "WORKLOAD_AUTHORITY_UNAVAILABLE"
                continue
            cached = next((row for row in self.rows(cycle) if row["id"] == str(job.id)), None)
            if cached and cached["manifest"] is not None:
                continue
            io = self.io_factory(cycle, job)
            try:
                with (
                    self.workflow_connection() as connection,
                    self.release_connection() as release_connection,
                ):
                    await seal_technical_recipe_revision(
                        WeeklyDeliveryConnection(
                            connection,
                            handle=job.handle,
                            site_id=UUID(cycle.site.site_id),
                            generation=generation,
                        ),
                        release_connection,
                        session_token=job.handle,
                        current_recovery_generation=generation,
                        site_id=UUID(cycle.site.site_id),
                        extension_id=job.extension_id,
                        report_id=job.report_id,
                        finding_id=job.finding_id,
                        recipe_key=job.recipe_key,
                        recipe_release_id=job.recipe_release_id,
                        idempotency_key=job.revision_key,
                        build_idempotency_key=job.build_key,
                        credential=io.credential,
                        github_transport=io.github_read_transport,
                        runner=io.runner,
                        model=io.model,
                        openbao_transport=io.openbao_transport,
                    )
            except TechnicalRecipeUnavailable as error:
                self.prepare_detail = error.code
                failures.append(f"finding:{job.finding_id}/{error.code}")
        self.prepare_failure_refs = tuple(failures)
        return tuple(
            self._candidate(cycle, row) for row in self.rows(cycle) if row["manifest"] is not None
        )

    async def dispatch(
        self, cycle: WeeklyCycle, decisions: tuple[str, ...], *, reconcile_only=False
    ) -> WeeklyStageResult:
        generation = (await self.recovery_source.current_generation()).value
        refs = []
        waiting = False
        unknown = False
        jobs = {row["id"]: row for row in self.rows(cycle)}
        jobs.update({row["id"]: row for row in self.backlog(cycle)})
        for row in jobs.values():
            if row["manifest"] is None:
                continue
            job = _workload(cycle, row)
            io = self.io_factory(cycle, job)
            with (
                self.workflow_connection() as connection,
                self.release_connection() as release_connection,
            ):
                scoped = WeeklyDeliveryConnection(
                    connection,
                    handle=job.handle,
                    site_id=UUID(cycle.site.site_id),
                    generation=generation,
                )
                operations = read_authenticated_github_pr_operations(
                    scoped,
                    session_token=job.handle,
                    requested_site_id=UUID(cycle.site.site_id),
                    current_recovery_generation=generation,
                )
                operation = next((item for item in operations if item.id == job.operation_id), None)
                if operation and operation.state in {"dispatching", "outcome_unknown"}:
                    try:
                        operation = await reconcile_weekly_operation(
                            scoped,
                            handle=job.handle,
                            operation_id=job.operation_id,
                            revision_id=job.revision_id,
                            extension_id=job.extension_id,
                            worker_id=uuid4(),
                            credential=io.credential,
                            egress=io.github_egress,
                            openbao_transport=io.openbao_transport,
                        )
                    except GitHubPrDeliveryUnavailable:
                        unknown = True
                        refs.append(f"operation:{job.operation_id}")
                        continue
                if operation and operation.state == "opened":
                    refs.append(f"operation:{operation.id}")
                    continue
                if reconcile_only:
                    refs.append(f"revision:{row['revision_sha256']}")
                    continue
                if str(job.gate_decision_id) in decisions:
                    with _clean_transaction(connection):
                        authorization = connection.execute(
                            "SELECT control.authorize_weekly_dispatch(%s,%s,%s,%s)",
                            (
                                scoped.handle_hash,
                                scoped.site_id,
                                generation,
                                stable_identity(f"{job.id}:standing-dispatch"),
                            ),
                        ).fetchone()
                        if authorization and authorization[0] is not None:
                            connection.execute(
                                "SELECT control.resolve_weekly_deferral(%s,%s,%s,%s,%s,%s)",
                                (
                                    UUID(cycle.site.tenant_id),
                                    scoped.site_id,
                                    cycle.week_start,
                                    UUID(cycle.cycle_id),
                                    bytes.fromhex(row["revision_sha256"]),
                                    job.gate_decision_id,
                                ),
                            ).fetchone()
                try:
                    operation = await open_approved_github_pr(
                        scoped,
                        release_connection,
                        session_token=job.handle,
                        current_recovery_generation=generation,
                        site_id=scoped.site_id,
                        revision_id=job.revision_id,
                        expected_revision_sha256=row["revision_sha256"],
                        operation_id=job.operation_id,
                        worker_id=uuid4(),
                        credential=io.credential,
                        github_read_transport=io.github_read_transport,
                        github_write_egress=io.github_egress,
                        journal=io.journal,
                        openbao_transport=io.openbao_transport,
                    )
                    refs.append(f"operation:{operation.id}")
                except PermissionError:
                    waiting = True
                    refs.append(f"revision:{row['revision_sha256']}")
                except GitHubPrDeliveryUnavailable as error:
                    unknown |= error.code == "OUTCOME_UNKNOWN"
                    waiting = True
                    refs.append(f"operation:{job.operation_id}")
        return WeeklyStageResult(
            "stopped"
            if reconcile_only
            else "waiting_owner"
            if waiting
            else "completed"
            if refs
            else "unavailable",
            "IN_FLIGHT_OUTCOME_UNKNOWN"
            if unknown
            else "AUTHORITY_CHANGED"
            if reconcile_only
            else "EXACT_OWNER_REVIEW_REQUIRED"
            if waiting
            else "DELIVERY_HANDOFF_RECORDED",
            tuple(refs),
        )

    async def run(self, stage: WeeklyStage) -> WeeklyStageResult:
        if stage.name == "measure":
            from signal_core.change_measurement import WeeklyChangeMeasurements

            return await WeeklyChangeMeasurements(
                self.workflow_connection, self.recovery_source
            ).run(stage)
        if stage.name == "report":
            return WeeklyStageResult(
                "completed",
                "COMMITTED_DELIVERY_REPORT",
                tuple(
                    f"revision:{row['revision_sha256']}"
                    for row in self.rows(stage.cycle)
                    if row["revision_sha256"] is not None
                ),
            )
        if stage.name != "verify":
            raise ValueError("Delivery handles only verify, measure, and report stages.")
        generation = (await self.recovery_source.current_generation()).value
        refs, outcomes = [], []
        for row in self.backlog(stage.cycle):
            if row["manifest"] is None:
                continue
            job = _workload(stage.cycle, row)
            io = self.io_factory(stage.cycle, job)
            with self.workflow_connection() as connection:
                scoped = WeeklyDeliveryConnection(
                    connection,
                    handle=job.handle,
                    site_id=UUID(stage.cycle.site.site_id),
                    generation=generation,
                )
                previous = read_authenticated_github_delivery_observations(
                    scoped,
                    session_token=job.handle,
                    requested_site_id=scoped.site_id,
                    current_recovery_generation=generation,
                )
                attempt = (
                    previous[0].attempt_id
                    if previous and previous[0].state == "dispatching"
                    else uuid4()
                )
                try:
                    result = await observe_github_delivery(
                        scoped,
                        session_token=job.handle,
                        site_id=scoped.site_id,
                        current_recovery_generation=generation,
                        operation_id=job.operation_id,
                        attempt_id=attempt,
                        environment=io.environment,
                        trusted_deployment_actor_id=io.trusted_deployment_actor_id,
                        credential=io.credential,
                        github_egress=io.github_egress,
                        live_verifier=io.live_verifier,
                        openbao_transport=io.openbao_transport,
                    )
                    outcomes.append(result["outcome"])
                    refs.append(f"observation:{attempt}")
                except GitHubObservationUnavailable:
                    outcomes.append("unavailable")
                    refs.append(f"operation:{job.operation_id}")
        return WeeklyStageResult(
            "completed"
            if outcomes and all(item == "verified" for item in outcomes)
            else "deferred"
            if outcomes
            else "unavailable",
            "LIVE_VERIFIED"
            if outcomes and all(item == "verified" for item in outcomes)
            else "DELIVERY_OBSERVATION_PENDING",
            tuple(refs),
        )


class DeliveryWeeklyWork:
    def __init__(self, crawl_work, delivery: WeeklyTechnicalDelivery):
        self.crawl_work = crawl_work
        self.delivery = delivery

    async def run(self, stage: WeeklyStage) -> WeeklyStageResult:
        if stage.name in {"observe", "analyze", "plan"}:
            return await self.crawl_work.run(stage)
        return await self.delivery.run(stage)
