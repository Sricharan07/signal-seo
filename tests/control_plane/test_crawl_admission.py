import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from signal_core.commands import accept_snapshot
from signal_core.crawl_admission import (
    OriginAdmissionPolicy,
    OriginPermitCompletion,
    OriginPermitConflict,
    OriginPermitDeferred,
    OriginPermitExpired,
    OriginPermitGrant,
    OriginPermitReplay,
    OriginPermitUnavailable,
    acquire_origin_permit,
    finish_origin_permit,
    reconcile_expired_origin_permits,
)
from signal_core.crawl_frontier import (
    CrawlRunLimits,
    claim_crawl_frontier,
    open_crawl_run,
)
from signal_core.crawl_urls import CrawlScopePolicy, normalize_crawl_url
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started


def frontier_lease(api, scheduler, workflow, crawl_admission, scope, key, origin):
    command = accept_snapshot(
        api,
        scope,
        actor_service="origin-admission-test",
        idempotency_key=key,
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.origin-admission",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admission,
        WorkflowStartReceipt(admission.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    crawl_input = CrawlSiteWorkflowInput(
        schema_version=1,
        tenant_id=str(scope.tenant_id),
        site_id=str(scope.site_id),
        command_id=str(command.id),
        source_event_id=str(admission.source_event_id),
        scope_version=1,
        crawl_policy_version=1,
    )
    run = open_crawl_run(
        crawl_admission,
        crawl_input,
        first_run_id=started.first_run_id,
        policy=CrawlScopePolicy(
            schema_version=1,
            allowed_origins=(origin,),
            user_agent="SignalBot/1.0 (+https://signal.example/bot)",
            request_timeout_seconds=2,
            total_timeout_seconds=5,
        ),
        limits=CrawlRunLimits(),
        seed_url=f"{origin}/",
    )
    lease = claim_crawl_frontier(
        crawl_admission,
        run,
        worker_key=f"crawler.{key}",
        lease_id=uuid4(),
        lease_seconds=60,
    )
    assert lease is not None
    return lease


def test_admission_persists_one_global_bucket_and_opaque_active_lease(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "permit-persist",
        "https://shared-admission.example",
    )
    policy = OriginAdmissionPolicy(min_delay_ms=1500, permit_lease_seconds=45)
    grant = acquire_origin_permit(
        crawl_admission,
        frontier,
        permit_id=uuid4(),
        permit_kind="robots",
        policy=policy,
    )

    assert isinstance(grant, OriginPermitGrant)
    assert grant.origin == "https://shared-admission.example"
    assert grant.expires_at <= frontier.lease_until
    assert grant.authority_fingerprint_sha256 not in repr(grant)
    assert grant.origin not in repr(grant)
    assert admin.execute(
        "SELECT origin, profile_version, request_tokens, in_flight_count, "
        "next_allowed_at >= %s + interval '1500 milliseconds', degraded_until "
        "FROM control.origin_buckets WHERE id = %s",
        (grant.issued_at, grant.bucket_id),
    ).fetchone() == ("https://shared-admission.example", 1, 0, 1, True, None)
    lease_row = admin.execute(
        "SELECT workload_id, permit_kind, octet_length(authority_fingerprint), "
        "min_delay_ms, requested_lease_seconds, released_at "
        "FROM control.admission_leases WHERE id = %s",
        (grant.permit_id,),
    ).fetchone()
    assert lease_row == (grant.workload_id, "robots", 32, 1500, 45, None)
    assert str(scopes[0].tenant_id) not in grant.workload_id
    assert str(scopes[0].site_id) not in grant.workload_id
    columns = {
        row[0]
        for row in admin.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'control' AND table_name IN "
            "('origin_buckets', 'admission_leases')"
        )
    }
    assert "tenant_id" not in columns
    assert "site_id" not in columns


def test_runtime_has_function_only_access_and_leases_reject_direct_mutation(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "permit-privilege",
        "https://privilege-admission.example",
    )
    grant = acquire_origin_permit(
        crawl_admission,
        frontier,
        permit_id=uuid4(),
        permit_kind="robots",
        policy=OriginAdmissionPolicy(),
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        crawl_admission.execute("SELECT * FROM control.origin_buckets").fetchall()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        crawl_admission.execute("UPDATE control.admission_leases SET released_at = now()")
    for statement in [
        "DELETE FROM control.admission_leases WHERE id = %s",
        "UPDATE control.admission_leases SET workload_id = 'crawl:changed' WHERE id = %s",
    ]:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(statement, (grant.permit_id,))
    signature = (
        "control.acquire_origin_permit(uuid,uuid,uuid,uuid,uuid,text,uuid,text,"
        "integer,text,text,integer,integer)"
    )
    assert admin.execute(
        "SELECT has_function_privilege('signal_crawl_admission', %s, 'EXECUTE')",
        (signature,),
    ).fetchone() == (True,)
    assert admin.execute(
        "SELECT has_function_privilege('signal_api', %s, 'EXECUTE')",
        (signature,),
    ).fetchone() == (False,)


def test_database_contract_rejects_null_policy_and_completion_fields(
    api, scheduler, workflow, crawl_admission, scopes
):
    frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "permit-null-contract",
        "https://null-contract.example",
    )
    workload_id = f"crawl:{frontier.run_id}:{frontier.lease_id}"
    acquire_values = [
        frontier.tenant_id,
        frontier.site_id,
        frontier.run_id,
        frontier.frontier_id,
        frontier.lease_id,
        frontier.lease_owner,
        uuid4(),
        frontier.url.origin,
        1,
        workload_id,
        "robots",
        1000,
        30,
    ]
    for index in (10, 11, 12):
        invalid = acquire_values.copy()
        invalid[index] = None
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            crawl_admission.execute(
                "SELECT * FROM control.acquire_origin_permit(" + ", ".join(["%s"] * 13) + ")",
                invalid,
            ).fetchone()

    grant = acquire_origin_permit(
        crawl_admission,
        frontier,
        permit_id=uuid4(),
        permit_kind="robots",
        policy=OriginAdmissionPolicy(),
    )
    finish_values = [
        grant.tenant_id,
        grant.site_id,
        grant.crawl_run_id,
        grant.frontier_id,
        grant.frontier_lease_id,
        grant.worker_key,
        grant.permit_id,
        grant.bucket_id,
        grant.origin,
        grant.profile_version,
        grant.workload_id,
        grant.permit_kind,
        grant.issued_at,
        grant.expires_at,
        "success",
        1,
        None,
    ]
    for index in (11, 14, 15):
        invalid = finish_values.copy()
        invalid[index] = None
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            crawl_admission.execute(
                "SELECT * FROM control.finish_origin_permit(" + ", ".join(["%s"] * 17) + ")",
                invalid,
            ).fetchone()
    rate_limited = finish_values.copy()
    rate_limited[14] = "rate_limited"
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        crawl_admission.execute(
            "SELECT * FROM control.finish_origin_permit(" + ", ".join(["%s"] * 17) + ")",
            rate_limited,
        ).fetchone()


def test_exact_acquire_retry_replays_active_grant_and_rejects_rebinding(
    api, scheduler, workflow, crawl_admission, scopes
):
    frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "permit-retry",
        "https://retry-admission.example",
    )
    permit_id = uuid4()
    policy = OriginAdmissionPolicy(min_delay_ms=1200)
    first = acquire_origin_permit(
        crawl_admission,
        frontier,
        permit_id=permit_id,
        permit_kind="robots",
        policy=policy,
    )
    duplicate = acquire_origin_permit(
        crawl_admission,
        frontier,
        permit_id=permit_id,
        permit_kind="robots",
        policy=policy,
    )
    assert duplicate == replace(first, duplicate=True)
    with pytest.raises(OriginPermitConflict):
        acquire_origin_permit(
            crawl_admission,
            frontier,
            permit_id=permit_id,
            permit_kind="robots",
            policy=OriginAdmissionPolicy(min_delay_ms=1300),
        )
    with pytest.raises(OriginPermitConflict):
        acquire_origin_permit(
            crawl_admission,
            frontier,
            permit_id=uuid4(),
            permit_kind="robots",
            policy=policy,
        )


def test_concurrent_exact_acquire_retry_returns_one_logical_grant(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    origin = "https://concurrent-retry-admission.example"
    frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "permit-concurrent-retry",
        origin,
    )
    permit_id = uuid4()
    policy = OriginAdmissionPolicy(min_delay_ms=1200)
    bucket_id = admin.execute(
        "INSERT INTO control.origin_buckets (origin, profile_version) VALUES (%s, 1) RETURNING id",
        (origin,),
    ).fetchone()[0]
    application_prefix = f"signal-origin-retry-{permit_id}"
    barrier = Barrier(2, timeout=10)

    def acquire(index):
        with psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"],
            autocommit=True,
            application_name=f"{application_prefix}-{index}",
        ) as connection:
            barrier.wait()
            return acquire_origin_permit(
                connection,
                frontier,
                permit_id=permit_id,
                permit_kind="robots",
                policy=policy,
            )

    with psycopg.connect(os.environ["SIGNAL_TEST_ADMIN_DSN"]) as blocker:
        blocker.execute(
            "SELECT id FROM control.origin_buckets WHERE id = %s FOR UPDATE",
            (bucket_id,),
        ).fetchone()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(acquire, index) for index in range(2)]
            deadline = time.monotonic() + 5
            blocked = 0
            while blocked < 2 and time.monotonic() < deadline:
                blocked = admin.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name LIKE %s AND wait_event_type = 'Lock'",
                    (f"{application_prefix}%",),
                ).fetchone()[0]
                time.sleep(0.01)
            assert blocked == 2
            blocker.commit()
            results = [future.result(timeout=10) for future in futures]

    original = next(result for result in results if result.duplicate is False)
    duplicate = next(result for result in results if result.duplicate is True)
    assert isinstance(original, OriginPermitGrant)
    assert duplicate == replace(original, duplicate=True)


def test_same_origin_is_serialized_across_tenants_under_concurrency(
    api, scheduler, workflow, scopes
):
    origin = "https://concurrent-admission.example"
    with psycopg.connect(
        os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
    ) as setup_admission:
        first_frontier = frontier_lease(
            api,
            scheduler,
            workflow,
            setup_admission,
            scopes[0],
            "permit-race-first",
            origin,
        )
        second_frontier = frontier_lease(
            api,
            scheduler,
            workflow,
            setup_admission,
            scopes[2],
            "permit-race-second",
            origin,
        )
    barrier = Barrier(2, timeout=10)

    def acquire(frontier):
        with psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
        ) as connection:
            barrier.wait()
            return acquire_origin_permit(
                connection,
                frontier,
                permit_id=uuid4(),
                permit_kind="robots",
                policy=OriginAdmissionPolicy(),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, (first_frontier, second_frontier)))

    assert sum(isinstance(result, OriginPermitGrant) for result in results) == 1
    deferred = next(result for result in results if isinstance(result, OriginPermitDeferred))
    assert deferred.reason == "in_flight"
    assert deferred.active_in_flight == 1


def test_distinct_origins_have_independent_buckets(
    api, scheduler, workflow, crawl_admission, scopes
):
    frontiers = [
        frontier_lease(
            api,
            scheduler,
            workflow,
            crawl_admission,
            scopes[index],
            f"permit-independent-{index}",
            f"https://independent-{index}.example",
        )
        for index in (0, 2)
    ]
    grants = [
        acquire_origin_permit(
            crawl_admission,
            frontier,
            permit_id=uuid4(),
            permit_kind="robots",
            policy=OriginAdmissionPolicy(),
        )
        for frontier in frontiers
    ]
    assert all(isinstance(grant, OriginPermitGrant) for grant in grants)
    assert len({grant.bucket_id for grant in grants}) == 2


def test_successful_finish_is_retry_safe_and_preserves_shared_delay(
    api, scheduler, workflow, crawl_admission, scopes
):
    origin = "https://finish-admission.example"
    first_frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "permit-finish-first",
        origin,
    )
    second_frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[2],
        "permit-finish-second",
        origin,
    )
    grant = acquire_origin_permit(
        crawl_admission,
        first_frontier,
        permit_id=uuid4(),
        permit_kind="robots",
        policy=OriginAdmissionPolicy(min_delay_ms=5000),
    )
    completion = OriginPermitCompletion("success", 100)
    first = finish_origin_permit(crawl_admission, grant, completion)
    duplicate = finish_origin_permit(crawl_admission, grant, completion)
    assert first.active_in_flight == 0
    assert duplicate == replace(first, duplicate=True)
    deferred = acquire_origin_permit(
        crawl_admission,
        second_frontier,
        permit_id=uuid4(),
        permit_kind="robots",
        policy=OriginAdmissionPolicy(),
    )
    assert isinstance(deferred, OriginPermitDeferred)
    assert deferred.reason == "politeness"
    assert deferred.retry_at == first.next_allowed_at
    with pytest.raises(OriginPermitConflict):
        finish_origin_permit(
            crawl_admission,
            grant,
            OriginPermitCompletion("cancelled", 100),
        )


@pytest.mark.parametrize(
    ("completion", "minimum_delay_ms", "degraded"),
    [
        (OriginPermitCompletion("rate_limited", 25, 7000), 7000, True),
        (OriginPermitCompletion("service_unavailable", 25), 30_000, True),
        (OriginPermitCompletion("transport_error", 25), 5000, False),
        (OriginPermitCompletion("success", 3000), 3000, False),
    ],
)
def test_completion_applies_global_provider_and_latency_backoff(
    api,
    scheduler,
    workflow,
    crawl_admission,
    scopes,
    completion,
    minimum_delay_ms,
    degraded,
):
    origin = f"https://backoff-{completion.kind.replace('_', '-')}.example"
    frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        f"permit-backoff-{completion.kind}",
        origin,
    )
    grant = acquire_origin_permit(
        crawl_admission,
        frontier,
        permit_id=uuid4(),
        permit_kind="html_navigation",
        policy=OriginAdmissionPolicy(),
    )
    finished = finish_origin_permit(crawl_admission, grant, completion)
    assert finished.next_allowed_at >= finished.finished_at + timedelta(
        milliseconds=minimum_delay_ms
    )
    assert (finished.degraded_until is not None) is degraded


def test_expired_worker_permit_is_reconciled_and_cannot_be_finished(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "permit-expired",
        "https://expired-admission.example",
    )
    policy = OriginAdmissionPolicy(permit_lease_seconds=1)
    grant = acquire_origin_permit(
        crawl_admission,
        frontier,
        permit_id=uuid4(),
        permit_kind="robots",
        policy=policy,
    )
    time.sleep(1.05)
    result = reconcile_expired_origin_permits(crawl_admission, limit=1)
    assert result.reconciled_count == 1
    assert admin.execute(
        "SELECT completion_kind FROM control.admission_leases WHERE id = %s",
        (grant.permit_id,),
    ).fetchone() == ("lease_expired",)
    assert admin.execute(
        "SELECT in_flight_count, request_tokens, last_refill_at > %s "
        "FROM control.origin_buckets WHERE id = %s",
        (grant.issued_at, grant.bucket_id),
    ).fetchone() == (0, 1, True)
    replay = acquire_origin_permit(
        crawl_admission,
        frontier,
        permit_id=grant.permit_id,
        permit_kind="robots",
        policy=policy,
    )
    assert isinstance(replay, OriginPermitReplay)
    assert replay.completion_kind == "lease_expired"
    with pytest.raises(OriginPermitExpired):
        finish_origin_permit(
            crawl_admission,
            grant,
            OriginPermitCompletion("success", 100),
        )


def test_new_permit_requires_exact_live_frontier_scope_and_origin(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "permit-authority",
        "https://authority-admission.example",
    )
    wrong_scope = replace(frontier, tenant_id=scopes[2].tenant_id)
    wrong_origin = replace(
        frontier,
        url=normalize_crawl_url("https://other-admission.example/"),
    )
    for forged in (wrong_scope, wrong_origin):
        with pytest.raises(OriginPermitUnavailable):
            acquire_origin_permit(
                crawl_admission,
                forged,
                permit_id=uuid4(),
                permit_kind="robots",
                policy=OriginAdmissionPolicy(),
            )
    assert admin.execute(
        "SELECT count(*) FROM control.origin_buckets WHERE origin IN "
        "('https://authority-admission.example', 'https://other-admission.example')"
    ).fetchone() == (0,)


def test_authority_reduction_blocks_new_and_replayed_active_grants_but_not_release(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    new_frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "permit-reduced-new",
        "https://reduced-new.example",
    )
    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    with pytest.raises(OriginPermitUnavailable):
        acquire_origin_permit(
            crawl_admission,
            new_frontier,
            permit_id=uuid4(),
            permit_kind="robots",
            policy=OriginAdmissionPolicy(),
        )
    assert admin.execute(
        "SELECT count(*) FROM control.origin_buckets WHERE origin = 'https://reduced-new.example'"
    ).fetchone() == (0,)

    replay_frontier = frontier_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[2],
        "permit-reduced-replay",
        "https://reduced-replay.example",
    )
    permit_id = uuid4()
    grant = acquire_origin_permit(
        crawl_admission,
        replay_frontier,
        permit_id=permit_id,
        permit_kind="robots",
        policy=OriginAdmissionPolicy(),
    )
    admin.execute(
        "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[2].tenant_id,),
    )
    with pytest.raises(OriginPermitUnavailable):
        acquire_origin_permit(
            crawl_admission,
            replay_frontier,
            permit_id=permit_id,
            permit_kind="robots",
            policy=OriginAdmissionPolicy(),
        )
    assert admin.execute(
        "SELECT released_at FROM control.admission_leases WHERE id = %s",
        (permit_id,),
    ).fetchone() == (None,)
    finished = finish_origin_permit(
        crawl_admission,
        grant,
        OriginPermitCompletion("cancelled", 0),
    )
    assert finished.active_in_flight == 0


def test_reconciliation_is_bounded_and_rejects_invalid_limit(
    api, scheduler, workflow, crawl_admission, scopes
):
    grants = []
    for index in (0, 2):
        frontier = frontier_lease(
            api,
            scheduler,
            workflow,
            crawl_admission,
            scopes[index],
            f"permit-reconcile-{index}",
            f"https://reconcile-{index}.example",
        )
        grants.append(
            acquire_origin_permit(
                crawl_admission,
                frontier,
                permit_id=uuid4(),
                permit_kind="robots",
                policy=OriginAdmissionPolicy(permit_lease_seconds=1),
            )
        )
    assert all(isinstance(grant, OriginPermitGrant) for grant in grants)
    time.sleep(1.05)
    assert reconcile_expired_origin_permits(crawl_admission, limit=1).reconciled_count == 1
    assert reconcile_expired_origin_permits(crawl_admission, limit=1).reconciled_count == 1
    assert reconcile_expired_origin_permits(crawl_admission, limit=1).reconciled_count == 0
    with pytest.raises(ValueError, match="reconciliation limit"):
        reconcile_expired_origin_permits(crawl_admission, limit=0)
