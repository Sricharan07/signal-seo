import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from signal_core.commands import accept_snapshot
from signal_core.crawl_frontier import (
    CrawlFrontierRejected,
    CrawlFrontierUnavailable,
    CrawlRunConflict,
    CrawlRunLimits,
    CrawlRunUnavailable,
    claim_crawl_frontier,
    enqueue_crawl_url,
    open_crawl_run,
)
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started


def policy(**overrides):
    values = {
        "schema_version": 1,
        "allowed_origins": ("https://static.crawl.example", "https://crawl.example"),
        "user_agent": "SignalBot/1.0 (+https://signal.example/bot)",
        "request_timeout_seconds": 2,
        "total_timeout_seconds": 5,
        **overrides,
    }
    return CrawlScopePolicy(**values)


def limits(**overrides):
    return CrawlRunLimits(**overrides)


def running_command(api, scheduler, workflow, scope, key):
    command = accept_snapshot(
        api,
        scope,
        actor_service="crawl-frontier-test",
        idempotency_key=key,
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.crawl-frontier",
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
    return crawl_input, started


def opened_run(api, scheduler, workflow, crawl_admission, scope, key, **limit_overrides):
    crawl_input, started = running_command(api, scheduler, workflow, scope, key)
    opened = open_crawl_run(
        crawl_admission,
        crawl_input,
        first_run_id=started.first_run_id,
        policy=policy(),
        limits=limits(**limit_overrides),
        seed_url="HTTPS://CRAWL.EXAMPLE/#start",
    )
    return crawl_input, started, opened


def test_open_run_persists_immutable_scope_root_and_transport_profile(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    crawl_input, started, opened = opened_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "open-crawl-run"
    )

    assert opened.tenant_id == scopes[0].tenant_id
    assert opened.site_id == scopes[0].site_id
    assert opened.command_id == UUID(crawl_input.command_id)
    assert opened.first_run_id == started.first_run_id
    assert opened.status == "running"
    assert opened.duplicate is False
    assert "start" not in repr(opened)
    run_row = admin.execute(
        "SELECT command_id, workflow_id, first_run_id, scope_version, "
        "crawl_policy_version, scope_snapshot, limits_snapshot, "
        "octet_length(fetch_profile_hash), status, coverage_summary "
        "FROM app.crawl_runs WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, opened.run_id),
    ).fetchone()
    assert run_row == (
        opened.command_id,
        f"signal:CrawlSite:{scopes[0].tenant_id}:{opened.command_id}",
        started.first_run_id,
        1,
        1,
        {
            "allowed_origins": ["https://crawl.example", "https://static.crawl.example"],
            "authorized_content_classes": ["public_html"],
            "normalization_version": 1,
            "purpose": "connected_site_audit",
            "schema_version": 1,
            "seed_urls": ["https://crawl.example/"],
            "user_agent": "SignalBot/1.0 (+https://signal.example/bot)",
        },
        {
            "max_attempts_per_url": 3,
            "max_body_bytes": 5 * 1024 * 1024,
            "max_depth": 8,
            "max_duration_seconds": 3600,
            "max_redirects": 5,
            "max_total_bytes": 100 * 1024 * 1024,
            "max_urls": 1000,
            "request_timeout_ms": 2000,
            "schema_version": 1,
            "total_timeout_ms": 5000,
        },
        32,
        "running",
        None,
    )
    assert admin.execute(
        "SELECT url.original_url, url.fetch_url, url.normalized_key, url.origin, "
        "frontier.depth, frontier.discovery_reason, frontier.priority, frontier.status, "
        "frontier.attempt_count FROM app.urls AS url JOIN app.crawl_frontier AS frontier "
        "ON frontier.tenant_id = url.tenant_id AND frontier.site_id = url.site_id "
        "AND frontier.url_id = url.id WHERE frontier.tenant_id = %s AND frontier.id = %s",
        (scopes[0].tenant_id, opened.root_frontier_id),
    ).fetchone() == (
        "HTTPS://CRAWL.EXAMPLE/#start",
        "https://crawl.example/",
        "https://crawl.example/",
        "https://crawl.example",
        0,
        "root",
        1000,
        "pending",
        0,
    )


def test_open_run_is_idempotent_and_different_configuration_conflicts(
    api, scheduler, workflow, crawl_admission, scopes
):
    crawl_input, started = running_command(
        api, scheduler, workflow, scopes[0], "crawl-run-idempotency"
    )
    arguments = {
        "first_run_id": started.first_run_id,
        "policy": policy(),
        "limits": limits(),
        "seed_url": "https://crawl.example/",
    }
    first = open_crawl_run(crawl_admission, crawl_input, **arguments)
    duplicate = open_crawl_run(crawl_admission, crawl_input, **arguments)
    assert duplicate == replace(first, duplicate=True)

    with pytest.raises(CrawlRunConflict):
        open_crawl_run(
            crawl_admission,
            crawl_input,
            **{**arguments, "limits": limits(max_urls=999)},
        )


def test_open_run_requires_the_exact_running_workflow(
    api, scheduler, workflow, crawl_admission, scopes
):
    crawl_input, _ = running_command(api, scheduler, workflow, scopes[0], "crawl-run-bound")
    with pytest.raises(CrawlRunUnavailable):
        open_crawl_run(
            crawl_admission,
            crawl_input,
            first_run_id=str(uuid4()),
            policy=policy(),
            limits=limits(),
            seed_url="https://crawl.example/",
        )


def test_concurrent_open_creates_one_run_and_one_root(api, scheduler, workflow, scopes):
    crawl_input, started = running_command(api, scheduler, workflow, scopes[0], "crawl-run-race")
    barrier = Barrier(2, timeout=10)

    def open_once():
        with psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
        ) as connection:
            barrier.wait()
            return open_crawl_run(
                connection,
                crawl_input,
                first_run_id=started.first_run_id,
                policy=policy(),
                limits=limits(),
                seed_url="https://crawl.example/",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: open_once(), range(2)))

    assert sorted(result.duplicate for result in results) == [False, True]
    assert len({result.run_id for result in results}) == 1
    assert len({result.root_frontier_id for result in results}) == 1


def test_enqueue_preserves_provenance_and_deduplicates_fetch_identity(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    _, _, opened = opened_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "enqueue-crawl-url"
    )
    first = enqueue_crawl_url(
        crawl_admission,
        opened,
        discovered_url="https://CRAWL.example/Docs/?b=2&a=1#first",
        discovered_from_url_id=opened.root_url_id,
        depth=1,
        discovery_reason="internal_link",
        priority=700,
    )
    duplicate = enqueue_crawl_url(
        crawl_admission,
        opened,
        discovered_url="https://crawl.example/Docs/?b=2&a=1#second",
        discovered_from_url_id=opened.root_url_id,
        depth=1,
        discovery_reason="internal_link",
        priority=1,
    )

    assert first.status == "pending"
    assert first.duplicate is False
    assert duplicate == replace(first, duplicate=True)
    assert "Docs" not in repr(first)
    assert admin.execute(
        "SELECT frontier.discovered_from_url_id, frontier.depth, "
        "frontier.discovery_reason, frontier.priority, url.original_url, url.fetch_url "
        "FROM app.crawl_frontier AS frontier JOIN app.urls AS url "
        "ON url.tenant_id = frontier.tenant_id AND url.site_id = frontier.site_id "
        "AND url.id = frontier.url_id WHERE frontier.tenant_id = %s AND frontier.id = %s",
        (scopes[0].tenant_id, first.frontier_id),
    ).fetchone() == (
        opened.root_url_id,
        1,
        "internal_link",
        700,
        "https://CRAWL.example/Docs/?b=2&a=1#first",
        "https://crawl.example/Docs/?b=2&a=1",
    )


def test_enqueue_retry_returns_original_receipt_after_the_item_is_leased(
    api, scheduler, workflow, crawl_admission, scopes
):
    _, _, opened = opened_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "enqueue-after-lease"
    )
    queued = enqueue_crawl_url(
        crawl_admission,
        opened,
        discovered_url="https://static.crawl.example/asset",
        discovered_from_url_id=opened.root_url_id,
        depth=1,
        discovery_reason="internal_link",
        priority=700,
    )
    root_lease = claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.root",
        lease_id=uuid4(),
        lease_seconds=30,
    )
    item_lease = claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.static",
        lease_id=uuid4(),
        lease_seconds=30,
    )
    assert root_lease is not None
    assert item_lease is not None
    assert item_lease.frontier_id == queued.frontier_id

    duplicate = enqueue_crawl_url(
        crawl_admission,
        opened,
        discovered_url="https://static.crawl.example/asset#retry",
        discovered_from_url_id=opened.root_url_id,
        depth=1,
        discovery_reason="internal_link",
        priority=1,
    )
    assert duplicate == replace(queued, duplicate=True)


def test_concurrent_enqueue_cannot_oversubscribe_the_url_budget(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    _, _, opened = opened_run(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "enqueue-budget-race",
        max_urls=2,
    )
    barrier = Barrier(2, timeout=10)

    def enqueue_once(index):
        with psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
        ) as connection:
            barrier.wait()
            try:
                return enqueue_crawl_url(
                    connection,
                    opened,
                    discovered_url=f"https://crawl.example/candidate-{index}",
                    discovered_from_url_id=opened.root_url_id,
                    depth=1,
                    discovery_reason="internal_link",
                    priority=500,
                )
            except CrawlFrontierRejected:
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(enqueue_once, range(2)))

    assert sum(result is not None for result in results) == 1
    assert admin.execute(
        "SELECT count(*) FROM app.crawl_frontier WHERE tenant_id = %s AND id <> %s "
        "AND crawl_run_id = %s",
        (scopes[0].tenant_id, opened.root_frontier_id, opened.run_id),
    ).fetchone() == (1,)


@pytest.mark.parametrize(
    ("url", "source", "depth", "reason", "limit_overrides", "exception"),
    [
        ("https://outside.example/", "root", 1, "internal_link", {}, CrawlFrontierRejected),
        ("https://crawl.example/deep", "root", 2, "internal_link", {}, CrawlFrontierRejected),
        (
            "https://crawl.example/full",
            "root",
            1,
            "internal_link",
            {"max_urls": 1},
            CrawlFrontierRejected,
        ),
        (
            "https://crawl.example/missing",
            "missing",
            1,
            "internal_link",
            {},
            CrawlFrontierUnavailable,
        ),
    ],
)
def test_enqueue_rejects_scope_depth_budget_and_missing_provenance(
    api,
    scheduler,
    workflow,
    crawl_admission,
    scopes,
    url,
    source,
    depth,
    reason,
    limit_overrides,
    exception,
):
    _, _, opened = opened_run(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        f"enqueue-reject-{uuid4()}",
        **limit_overrides,
    )
    source_id = opened.root_url_id if source == "root" else uuid4()
    with pytest.raises(exception):
        enqueue_crawl_url(
            crawl_admission,
            opened,
            discovered_url=url,
            discovered_from_url_id=source_id,
            depth=depth,
            discovery_reason=reason,
            priority=500,
        )


def test_claim_returns_bound_policy_exact_retry_and_stops_new_work_after_suspension(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    _, _, opened = opened_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "claim-crawl-root"
    )
    lease_id = uuid4()
    first = claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.worker-1",
        lease_id=lease_id,
        lease_seconds=30,
    )
    assert first is not None
    assert first.frontier_id == opened.root_frontier_id
    assert first.url.fetch_url == "https://crawl.example/"
    assert first.attempt_count == 1
    assert first.policy.allowed_origins == (
        "https://crawl.example",
        "https://static.crawl.example",
    )
    assert first.limits == limits()
    assert len(first.fetch_profile_sha256) == 64
    assert first.duplicate is False
    assert "crawl.example" not in repr(first)
    assert claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.worker-1",
        lease_id=lease_id,
        lease_seconds=30,
    ) == replace(first, duplicate=True)
    assert (
        claim_crawl_frontier(
            crawl_admission,
            opened,
            worker_key="crawler.worker-2",
            lease_id=uuid4(),
            lease_seconds=30,
        )
        is None
    )

    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    assert claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.worker-1",
        lease_id=lease_id,
        lease_seconds=30,
    ) == replace(first, duplicate=True)
    with pytest.raises(CrawlFrontierUnavailable):
        claim_crawl_frontier(
            crawl_admission,
            opened,
            worker_key="crawler.worker-2",
            lease_id=uuid4(),
            lease_seconds=30,
        )


def test_concurrent_claims_issue_only_one_origin_lease(
    api, scheduler, workflow, crawl_admission, scopes
):
    _, _, opened = opened_run(api, scheduler, workflow, crawl_admission, scopes[0], "claim-race")
    barrier = Barrier(2, timeout=10)

    def claim_once(index):
        with psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
        ) as connection:
            barrier.wait()
            return claim_crawl_frontier(
                connection,
                opened,
                worker_key=f"crawler.concurrent-{index}",
                lease_id=uuid4(),
                lease_seconds=30,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim_once, range(2)))

    assert sum(result is not None for result in results) == 1


def test_concurrent_same_lease_identity_cannot_cross_runs(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    _, _, first_run = opened_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "claim-identity-first"
    )
    _, _, second_run = opened_run(
        api, scheduler, workflow, crawl_admission, scopes[1], "claim-identity-second"
    )
    lease_id = uuid4()
    barrier = Barrier(2, timeout=10)

    def claim_once(run_and_index):
        run, index = run_and_index
        with psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
        ) as connection:
            barrier.wait()
            try:
                return claim_crawl_frontier(
                    connection,
                    run,
                    worker_key=f"crawler.identity-{index}",
                    lease_id=lease_id,
                    lease_seconds=30,
                )
            except CrawlFrontierUnavailable:
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim_once, [(first_run, 1), (second_run, 2)]))

    assert sum(result is not None for result in results) == 1
    assert admin.execute(
        "SELECT count(*) FROM app.crawl_frontier_leases WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, lease_id),
    ).fetchone() == (1,)


def test_expired_lease_is_reassigned_only_until_attempt_budget(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    _, _, opened = opened_run(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "claim-expiry",
        max_attempts_per_url=2,
    )
    first_lease_id = uuid4()
    first = claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.expiry-1",
        lease_id=first_lease_id,
        lease_seconds=1,
    )
    assert first is not None
    time.sleep(1.05)
    with pytest.raises(CrawlFrontierUnavailable):
        claim_crawl_frontier(
            crawl_admission,
            opened,
            worker_key="crawler.expiry-1",
            lease_id=first_lease_id,
            lease_seconds=1,
        )
    second_lease_id = uuid4()
    second = claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.expiry-2",
        lease_id=second_lease_id,
        lease_seconds=1,
    )
    assert second is not None
    assert second.frontier_id == first.frontier_id
    assert second.attempt_count == 2
    with pytest.raises(CrawlFrontierUnavailable):
        claim_crawl_frontier(
            crawl_admission,
            opened,
            worker_key="crawler.expiry-1",
            lease_id=first_lease_id,
            lease_seconds=1,
        )
    time.sleep(1.05)
    assert (
        claim_crawl_frontier(
            crawl_admission,
            opened,
            worker_key="crawler.expiry-3",
            lease_id=uuid4(),
            lease_seconds=1,
        )
        is None
    )
    assert admin.execute(
        "SELECT status, attempt_count, terminal_at IS NOT NULL "
        "FROM app.crawl_frontier WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, opened.root_frontier_id),
    ).fetchone() == ("permanently_failed", 2, True)
    assert admin.execute(
        "SELECT id, lease_owner, attempt_number FROM app.crawl_frontier_leases "
        "WHERE tenant_id = %s AND frontier_id = %s ORDER BY attempt_number",
        (scopes[0].tenant_id, opened.root_frontier_id),
    ).fetchall() == [
        (first_lease_id, "crawler.expiry-1", 1),
        (second_lease_id, "crawler.expiry-2", 2),
    ]


def test_duration_budget_stops_new_discovery_and_leases(
    api, scheduler, workflow, crawl_admission, scopes
):
    _, _, opened = opened_run(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "crawl-duration-budget",
        max_duration_seconds=1,
    )
    lease_id = uuid4()
    lease = claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.duration",
        lease_id=lease_id,
        lease_seconds=300,
    )
    assert lease is not None
    assert lease.lease_until <= opened.started_at + timedelta(seconds=1)
    time.sleep(1.05)

    with pytest.raises(CrawlFrontierRejected):
        enqueue_crawl_url(
            crawl_admission,
            opened,
            discovered_url="https://crawl.example/too-late",
            discovered_from_url_id=opened.root_url_id,
            depth=1,
            discovery_reason="internal_link",
            priority=500,
        )
    with pytest.raises(CrawlFrontierUnavailable):
        claim_crawl_frontier(
            crawl_admission,
            opened,
            worker_key="crawler.duration",
            lease_id=lease_id,
            lease_seconds=300,
        )
    with pytest.raises(CrawlFrontierUnavailable):
        claim_crawl_frontier(
            crawl_admission,
            opened,
            worker_key="crawler.duration",
            lease_id=uuid4(),
            lease_seconds=30,
        )


def test_accepted_enqueue_retry_survives_authority_reduction_but_new_discovery_does_not(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    _, _, opened = opened_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "enqueue-after-suspend"
    )
    first = enqueue_crawl_url(
        crawl_admission,
        opened,
        discovered_url="https://crawl.example/accepted",
        discovered_from_url_id=opened.root_url_id,
        depth=1,
        discovery_reason="internal_link",
        priority=500,
    )
    admin.execute(
        "UPDATE app.sites SET state = 'archived' WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    )
    duplicate = enqueue_crawl_url(
        crawl_admission,
        opened,
        discovered_url="https://crawl.example/accepted#again",
        discovered_from_url_id=opened.root_url_id,
        depth=1,
        discovery_reason="internal_link",
        priority=1,
    )
    assert duplicate == replace(first, duplicate=True)
    with pytest.raises(CrawlFrontierUnavailable):
        enqueue_crawl_url(
            crawl_admission,
            opened,
            discovered_url="https://crawl.example/new",
            discovered_from_url_id=opened.root_url_id,
            depth=1,
            discovery_reason="internal_link",
            priority=500,
        )


def test_crawl_role_is_function_only_and_helpers_are_private(admin, crawl_admission):
    signatures = [
        "control.open_crawl_run(uuid,uuid,uuid,text,text,uuid,integer,integer,jsonb,jsonb,bytea,uuid,uuid,text,text,text,text)",
        "control.enqueue_crawl_url(uuid,uuid,uuid,uuid,uuid,uuid,integer,text,integer,integer,text,text,text,text)",
        "control.claim_crawl_frontier(uuid,uuid,uuid,text,uuid,integer)",
    ]
    for signature in signatures:
        assert admin.execute(
            "SELECT has_function_privilege('signal_crawl_admission', %s, 'EXECUTE')",
            (signature,),
        ).fetchone() == (True,)
        for role in ("public", "signal_api", "signal_scheduler", "signal_workflow"):
            assert admin.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, signature)
            ).fetchone() == (False,)
    for helper in [
        "control.valid_crawl_url_identity(text,text,text,text,integer)",
        "control.valid_crawl_scope_snapshot(jsonb)",
        "control.valid_crawl_limits_snapshot(jsonb)",
    ]:
        assert admin.execute(
            "SELECT has_function_privilege('public', %s, 'EXECUTE')", (helper,)
        ).fetchone() == (False,)
    for table in ("crawl_runs", "urls", "crawl_frontier", "crawl_frontier_leases"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            crawl_admission.execute(f"SELECT * FROM app.{table}")


def test_crawl_tables_reject_direct_mutation_and_cross_site_provenance(
    admin, api, scheduler, workflow, crawl_admission, scopes
):
    _, _, opened = opened_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "crawl-table-guards"
    )
    _, _, other_opened = opened_run(
        api, scheduler, workflow, crawl_admission, scopes[1], "crawl-cross-site-source"
    )
    for statement, arguments in [
        (
            "UPDATE app.crawl_runs SET status = 'running' WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, opened.run_id),
        ),
        (
            "UPDATE app.urls SET fetch_url = fetch_url WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, opened.root_url_id),
        ),
        (
            "DELETE FROM app.crawl_frontier WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, opened.root_frontier_id),
        ),
    ]:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(statement, arguments)

    lease = claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.guard",
        lease_id=uuid4(),
        lease_seconds=30,
    )
    assert lease is not None
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.crawl_frontier_leases SET lease_owner = lease_owner "
            "WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, lease.lease_id),
        )

    orphan_url_id = uuid4()
    admin.execute(
        "INSERT INTO app.urls (tenant_id, site_id, id, original_url, fetch_url, "
        "normalized_key, origin, normalization_version) VALUES (%s, %s, %s, %s, %s, "
        "%s, %s, 1)",
        (
            scopes[0].tenant_id,
            scopes[0].site_id,
            orphan_url_id,
            "https://crawl.example/orphan",
            "https://crawl.example/orphan",
            "https://crawl.example/orphan",
            "https://crawl.example",
        ),
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin.execute(
            "INSERT INTO app.crawl_frontier (tenant_id, site_id, id, crawl_run_id, "
            "url_id, discovered_from_url_id, depth, discovery_reason, priority, "
            "next_attempt_at) VALUES (%s, %s, %s, %s, %s, %s, 1, "
            "'internal_link', 1, statement_timestamp())",
            (
                scopes[0].tenant_id,
                scopes[0].site_id,
                uuid4(),
                opened.run_id,
                orphan_url_id,
                other_opened.root_url_id,
            ),
        )


def test_crawl_wrappers_reject_non_autocommit_and_malformed_values(
    api, scheduler, workflow, scopes
):
    crawl_input, started = running_command(
        api, scheduler, workflow, scopes[0], "crawl-wrapper-validation"
    )
    with psycopg.connect(os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"]) as connection:
        with pytest.raises(ValueError, match="autocommit"):
            open_crawl_run(
                connection,
                crawl_input,
                first_run_id=started.first_run_id,
                policy=policy(),
                limits=limits(),
                seed_url="https://crawl.example/",
            )
    with pytest.raises(ValueError, match="millisecond"):
        open_crawl_run(
            workflow,
            crawl_input,
            first_run_id=started.first_run_id,
            policy=policy(request_timeout_seconds=0.3333),
            limits=limits(),
            seed_url="https://crawl.example/",
        )
    with pytest.raises(ValueError, match="byte budget"):
        open_crawl_run(
            workflow,
            crawl_input,
            first_run_id=started.first_run_id,
            policy=policy(),
            limits=limits(max_total_bytes=1024),
            seed_url="https://crawl.example/",
        )
