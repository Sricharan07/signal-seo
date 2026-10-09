import hashlib
import os
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.commands import accept_snapshot
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_audit import CrawlAuditUnavailable, analyze_crawl_manifest
from signal_core.crawl_frontier import CrawlRunLimits
from signal_core.crawl_http import EgressHttpResult
from signal_core.crawl_workflow_activities import CrawlExecutionRejected
from signal_core.full_site_crawl import FullSiteCrawlExecutor
from signal_core.origin_verification import (
    OriginProofObservation,
    issue_origin_challenge,
    prepare_origin_verification,
    record_origin_verification,
)
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.standing_authorization import (
    RecipeRange,
    StandingGrantRequest,
    grant_standing_authorization,
)
from signal_core.weekly_control import set_site_paused
from signal_core.weekly_loop import WeeklyCycle, WeeklySite
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started


def _command(api, scheduler, workflow, scope, key):
    command = accept_snapshot(api, scope, actor_service="full-crawl-test", idempotency_key=key)
    envelope = claim_outbox_batch(
        scheduler, tenant_id=scope.tenant_id, worker_key="worker.full-crawl", batch_size=1
    )[0]
    admitted = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admitted,
        WorkflowStartReceipt(admitted.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    return (
        CrawlSiteWorkflowInput(
            schema_version=1,
            tenant_id=str(scope.tenant_id),
            site_id=str(scope.site_id),
            command_id=str(command.id),
            source_event_id=str(admitted.source_event_id),
            scope_version=1,
            crawl_policy_version=1,
        ),
        started.first_run_id,
    )


def _verify_origin(admin, identity, identity_context, scope):
    admin.execute(
        "UPDATE app.memberships SET role_key = 'owner', authorization_epoch = 2 WHERE id = %s",
        (identity_context["membership_id"],),
    )
    origin = f"https://crawl-{scope.site_id}.example.invalid"
    admin.execute(
        "UPDATE app.sites SET primary_origin = %s WHERE tenant_id = %s AND id = %s",
        (origin, scope.tenant_id, scope.site_id),
    )
    authority = {
        "session_token": identity_context["session_token"],
        "current_recovery_generation": identity_context["generation"],
        "requested_site_id": scope.site_id,
        "origin": origin,
    }
    challenge = issue_origin_challenge(identity, **authority, idempotency_key=uuid4())
    request_id = uuid4()
    prepared = prepare_origin_verification(
        identity, **authority, challenge_id=challenge.challenge_id, idempotency_key=request_id
    )
    record_origin_verification(
        identity,
        **authority,
        challenge_id=challenge.challenge_id,
        idempotency_key=request_id,
        observation=OriginProofObservation(
            outcome="matched",
            http_status=200,
            media_type="text/plain",
            response_sha256=prepared.proof_sha256,
            final_url=prepared.proof_url,
            resolved_address="93.184.216.34",
            elapsed_ms=12,
        ),
    )
    return origin


class Fetcher:
    def __init__(
        self,
        origin,
        *,
        page_body=None,
        second_body=None,
        second_status=200,
        crash_on=None,
        after_robots=None,
        robots_status=404,
    ):
        self.origin = origin
        self.calls = []
        self.page_body = page_body
        self.second_body = second_body
        self.second_status = second_status
        self.crash_on = crash_on
        self.after_robots = after_robots
        self.robots_status = robots_status

    def request(self, outbound, *, policy):
        self.calls.append(outbound.url)
        if self.crash_on == outbound.url:
            raise RuntimeError("Synthetic worker crash after durable dispatch")
        if outbound.url == f"{self.origin}/robots.txt":
            status, body, media_type = self.robots_status, b"", "text/plain"
            if self.after_robots is not None:
                self.after_robots()
        else:
            status = self.second_status if outbound.url == f"{self.origin}/second" else 200
            body = (
                self.second_body
                if outbound.url == f"{self.origin}/second" and self.second_body is not None
                else self.page_body
                or b"<html><head><title>Home</title></head><body><h1>Hello</h1></body></html>"
            )
            media_type = "text/html"
        return EgressHttpResult(
            schema_version=1,
            request_url=outbound.url,
            final_url=outbound.url,
            method="GET",
            outcome="fetched",
            http_status=status,
            media_type=media_type,
            response_headers=(("content-type", media_type),),
            resolved_address="93.184.216.34",
            body=body,
            body_sha256=hashlib.sha256(body).hexdigest(),
            decoded_bytes=len(body),
            elapsed_ms=5,
        )


def _executor(tmp_path, fetcher):
    return FullSiteCrawlExecutor(
        admission_connection_factory=lambda: psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
        ),
        ingest_connection_factory=lambda: psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True
        ),
        store=EncryptedLocalArtifactStore(tmp_path / "objects"),
        artifact_key=ArtifactEncryptionKey("local-test-key", b"k" * 32),
        fetcher=fetcher,
        network_profile_sha256="ab" * 32,
        worker_key="worker.full-crawl",
        limits=CrawlRunLimits(max_urls=4, max_depth=1, max_duration_seconds=30),
    )


def test_verified_one_page_crawl_is_durable_and_replay_safe(
    admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, first_run_id = _command(api, scheduler, workflow, scope, "full-crawl-success")
    fetcher = Fetcher(origin)
    executor = _executor(tmp_path, fetcher)

    manifest = executor.run(command, first_run_id=first_run_id)
    replay = executor.run(command, first_run_id=first_run_id)

    assert replay == manifest
    assert manifest.coverage == "complete"
    assert (manifest.discovered_count, manifest.terminal_count) == (1, 1)
    assert fetcher.calls == [f"{origin}/robots.txt", f"{origin}/"]
    assert admin.execute(
        "SELECT title, headings, internal_links FROM app.crawl_page_records "
        "WHERE tenant_id = %s AND site_id = %s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == ("Home", [{"level": 1, "text": "Hello"}], [])
    body = b"<html><head><title>Home</title></head><body><h1>Hello</h1></body></html>"
    assert admin.execute(
        "SELECT terminal_state, decoded_bytes FROM app.crawl_frontier_settlements "
        "WHERE tenant_id = %s AND site_id = %s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == ("fetched", len(body))


def test_completed_crawl_audit_is_sealed_evidence_bound_and_replay_safe(
    admin, api, identity, scheduler, workflow, crawl_ingest, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, first_run_id = _command(api, scheduler, workflow, scope, "crawl-audit-report")
    fetcher = Fetcher(
        origin,
        page_body=(b"<html><head><title>Home</title></head><body><h1>Welcome</h1></body></html>"),
    )
    manifest = _executor(tmp_path, fetcher).run(command, first_run_id=first_run_id)
    first = analyze_crawl_manifest(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        manifest_id=UUID(manifest.manifest_id),
    )
    replay = analyze_crawl_manifest(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        manifest_id=UUID(manifest.manifest_id),
    )
    assert first.reused is True
    assert replay.reused is True
    assert first.findings == replay.findings
    assert {finding.key for finding in first.findings} == {
        "metadata.meta_description.missing",
        "canonical.missing",
    }
    assert first.input_count == 1
    assert first.coverage["sitemaps"] == "not_assessed"
    assert admin.execute(
        "SELECT finding_count, input_count FROM app.crawl_audit_reports "
        "WHERE tenant_id = %s AND site_id = %s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (2, 1)

    invalid_count = crawl_ingest.execute(
        "SELECT outcome FROM control.record_crawl_audit_report(" + ", ".join(["%s"] * 9) + ")",
        (
            scope.tenant_id,
            scope.site_id,
            UUID(manifest.manifest_id),
            first.id,
            first.detector_release_id,
            bytes.fromhex(first.manifest_sha256),
            Jsonb([finding.to_json() for finding in first.findings]),
            Jsonb(first.coverage),
            2,
        ),
    ).fetchone()
    assert invalid_count == ("invalid_report",)
    forged = [first.findings[0].to_json()]
    forged[0]["source_id"] = str(uuid4())
    assert crawl_ingest.execute(
        "SELECT outcome FROM control.record_crawl_audit_report(" + ", ".join(["%s"] * 9) + ")",
        (
            scope.tenant_id,
            scope.site_id,
            UUID(manifest.manifest_id),
            first.id,
            first.detector_release_id,
            bytes.fromhex(first.manifest_sha256),
            Jsonb(forged),
            Jsonb(first.coverage),
            1,
        ),
    ).fetchone() == ("invalid_evidence",)
    malformed = [first.findings[0].to_json()]
    malformed[0]["title"] = None
    assert crawl_ingest.execute(
        "SELECT outcome FROM control.record_crawl_audit_report(" + ", ".join(["%s"] * 9) + ")",
        (
            scope.tenant_id,
            scope.site_id,
            UUID(manifest.manifest_id),
            first.id,
            first.detector_release_id,
            bytes.fromhex(first.manifest_sha256),
            Jsonb(malformed),
            Jsonb(first.coverage),
            1,
        ),
    ).fetchone() == ("invalid_finding",)

    with pytest.raises(CrawlAuditUnavailable):
        analyze_crawl_manifest(
            crawl_ingest,
            tenant_id=scopes[1].tenant_id,
            site_id=scopes[1].site_id,
            manifest_id=UUID(manifest.manifest_id),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        crawl_ingest.execute("SELECT * FROM app.crawl_audit_reports")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        workflow.execute(
            "SELECT * FROM control.load_crawl_audit_inputs(%s, %s, %s)",
            (scope.tenant_id, scope.site_id, UUID(manifest.manifest_id)),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.crawl_audit_reports SET finding_count = 0 WHERE tenant_id = %s AND id = %s",
            (scope.tenant_id, first.id),
        )


def test_crawl_audit_reports_only_observed_broken_internal_target(
    api, identity, scheduler, workflow, crawl_ingest, admin, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, first_run_id = _command(api, scheduler, workflow, scope, "crawl-audit-broken")
    fetcher = Fetcher(
        origin,
        page_body=(
            b"<html><head><title>Home</title></head><body><h1>Home</h1>"
            b'<a href="/second">Gone</a></body></html>'
        ),
        second_body=b"<html><title>Not found</title></html>",
        second_status=404,
    )
    manifest = _executor(tmp_path, fetcher).run(command, first_run_id=first_run_id)
    report = analyze_crawl_manifest(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        manifest_id=UUID(manifest.manifest_id),
    )
    broken = [finding for finding in report.findings if finding.key == "links.internal.not_found"]
    assert manifest.coverage == "partial"
    assert len(broken) == 1
    assert broken[0].resource_locator == f"{origin}/second"
    assert broken[0].source_kind == "settlement"


def test_unverified_site_never_dispatches_network(api, scheduler, workflow, scopes, tmp_path):
    command, first_run_id = _command(api, scheduler, workflow, scopes[0], "full-crawl-unverified")
    fetcher = Fetcher("https://example.invalid")
    executor = _executor(tmp_path, fetcher)

    with pytest.raises(CrawlExecutionRejected):
        executor.run(command, first_run_id=first_run_id)

    assert fetcher.calls == []


def test_internal_link_expands_the_verified_frontier_without_external_fetch(
    admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, first_run_id = _command(api, scheduler, workflow, scope, "full-crawl-links")
    fetcher = Fetcher(
        origin,
        page_body=(
            b'<html><head><title>Home</title></head><body><a href="/second">'
            b'Second</a><a href="https://other.example/out">External</a></body></html>'
        ),
        second_body=b"<html><head><title>Second</title></head><body></body></html>",
    )

    manifest = _executor(tmp_path, fetcher).run(command, first_run_id=first_run_id)

    assert manifest.coverage == "complete"
    assert (manifest.discovered_count, manifest.terminal_count) == (2, 2)
    assert fetcher.calls == [
        f"{origin}/robots.txt",
        f"{origin}/",
        f"{origin}/second",
    ]
    assert admin.execute(
        "SELECT title, internal_links, external_links FROM app.crawl_page_records "
        "WHERE tenant_id = %s AND site_id = %s ORDER BY title",
        (scope.tenant_id, scope.site_id),
    ).fetchall() == [
        ("Home", [f"{origin}/second"], ["https://other.example/out"]),
        ("Second", [], []),
    ]


@pytest.mark.parametrize("crash_path", ["robots.txt", "page"])
def test_unknown_dispatch_is_partial_and_never_blindly_retried(
    crash_path,
    admin,
    api,
    identity,
    scheduler,
    workflow,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, first_run_id = _command(
        api, scheduler, workflow, scope, f"full-crawl-crash-{crash_path}"
    )
    crash_url = f"{origin}/robots.txt" if crash_path == "robots.txt" else f"{origin}/"
    fetcher = Fetcher(origin, crash_on=crash_url)
    executor = _executor(tmp_path, fetcher)

    with pytest.raises(RuntimeError, match="Synthetic worker crash"):
        executor.run(command, first_run_id=first_run_id)
    calls_before_recovery = list(fetcher.calls)
    if crash_path == "page":
        frontier_id, url_id, lease_id, run_id = admin.execute(
            "SELECT id, url_id, lease_id, crawl_run_id FROM app.crawl_frontier "
            "WHERE tenant_id = %s AND site_id = %s",
            (scope.tenant_id, scope.site_id),
        ).fetchone()
        operation_id = admin.execute(
            "SELECT id FROM app.egress_operations WHERE tenant_id = %s AND site_id = %s",
            (scope.tenant_id, scope.site_id),
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="invalid_settlement_egress"):
            crawl_ingest.execute(
                "SELECT * FROM control.settle_crawl_frontier(" + ", ".join(["%s"] * 14) + ")",
                (
                    scope.tenant_id,
                    scope.site_id,
                    run_id,
                    frontier_id,
                    url_id,
                    lease_id,
                    frontier_id,
                    "dispatch_unknown",
                    "dispatch_outcome_unknown",
                    operation_id,
                    uuid4(),
                    None,
                    None,
                    0,
                ),
            )

    manifest = executor.run(command, first_run_id=first_run_id)

    assert manifest.coverage == "partial"
    assert (manifest.discovered_count, manifest.terminal_count) == (1, 1)
    assert fetcher.calls == calls_before_recovery
    assert admin.execute(
        "SELECT terminal_state FROM app.crawl_frontier_settlements "
        "WHERE tenant_id = %s AND site_id = %s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == ("dispatch_unknown",)


def test_revocation_after_robots_prevents_page_dispatch(
    admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, first_run_id = _command(api, scheduler, workflow, scope, "full-crawl-revoked")

    def revoke():
        admin.execute(
            "UPDATE app.sites SET ownership_status = 'reverification_required' "
            "WHERE tenant_id = %s AND id = %s",
            (scope.tenant_id, scope.site_id),
        )

    fetcher = Fetcher(origin, after_robots=revoke)
    executor = _executor(tmp_path, fetcher)

    with pytest.raises(
        psycopg.errors.InsufficientPrivilege,
        match="verified_crawl_egress_unavailable",
    ):
        executor.run(command, first_run_id=first_run_id)

    assert fetcher.calls == [f"{origin}/robots.txt"]
    assert admin.execute(
        "SELECT count(*) FROM app.egress_operations WHERE tenant_id = %s AND site_id = %s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (0,)


def test_robots_denial_is_partial_and_never_fetches_page(
    admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, first_run_id = _command(api, scheduler, workflow, scope, "full-crawl-robots-deny")
    fetcher = Fetcher(origin, robots_status=403)

    manifest = _executor(tmp_path, fetcher).run(command, first_run_id=first_run_id)

    assert manifest.coverage == "partial"
    assert fetcher.calls == [f"{origin}/robots.txt"]
    assert admin.execute(
        "SELECT terminal_state FROM app.crawl_frontier_settlements "
        "WHERE tenant_id = %s AND site_id = %s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == ("robots_denied",)


def test_byte_limit_settles_remaining_frontier_as_partial(
    admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, first_run_id = _command(api, scheduler, workflow, scope, "full-crawl-budget")
    prefix = b'<html><head><title>Home</title></head><body><a href="/second">Second</a>'
    suffix = b"</body></html>"
    byte_limit = 5 * 1024 * 1024
    body = prefix + (b" " * (byte_limit - len(prefix) - len(suffix))) + suffix
    fetcher = Fetcher(origin, page_body=body)
    executor = FullSiteCrawlExecutor(
        admission_connection_factory=lambda: psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
        ),
        ingest_connection_factory=lambda: psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True
        ),
        store=EncryptedLocalArtifactStore(tmp_path / "objects"),
        artifact_key=ArtifactEncryptionKey("local-test-key", b"k" * 32),
        fetcher=fetcher,
        network_profile_sha256="ab" * 32,
        worker_key="worker.full-crawl",
        limits=CrawlRunLimits(
            max_urls=4, max_depth=1, max_total_bytes=byte_limit, max_duration_seconds=30
        ),
    )

    manifest = executor.run(command, first_run_id=first_run_id)

    assert manifest.coverage == "partial"
    assert (manifest.discovered_count, manifest.terminal_count) == (2, 2)
    assert fetcher.calls == [f"{origin}/robots.txt", f"{origin}/"]
    assert admin.execute(
        "SELECT terminal_state FROM app.crawl_frontier_settlements "
        "WHERE tenant_id = %s AND site_id = %s ORDER BY terminal_state",
        (scope.tenant_id, scope.site_id),
    ).fetchall() == [("budget_exhausted",), ("fetched",)]


def test_crawl_records_are_forced_rls_and_function_only(admin, crawl_ingest):
    for table in (
        "crawl_robots_dispatches",
        "crawl_page_records",
        "crawl_frontier_settlements",
        "crawl_manifests",
    ):
        assert admin.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"app.{table}",),
        ).fetchone() == (True, True)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            crawl_ingest.execute(f"SELECT * FROM app.{table}")
    for signature in (
        "control.record_crawl_page(uuid,uuid,uuid,uuid,uuid,uuid,uuid,integer,"
        "text,text,text,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,bytea,integer,boolean)",
        "control.settle_crawl_frontier(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,"
        "text,uuid,uuid,uuid,uuid,integer)",
        "control.finalize_crawl_run(uuid,uuid,uuid,uuid,bytea)",
    ):
        assert admin.execute(
            "SELECT has_function_privilege('signal_crawl_ingest', %s, 'EXECUTE')",
            (signature,),
        ).fetchone() == (True,)
        assert admin.execute(
            "SELECT has_function_privilege('signal_api', %s, 'EXECUTE')",
            (signature,),
        ).fetchone() == (False,)


def test_weekly_pause_after_robots_blocks_new_page_egress(
    admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    admin.execute(
        "UPDATE app.sites SET state='active' WHERE tenant_id=%s AND id=%s",
        (scope.tenant_id, scope.site_id),
    )
    now = admin.execute("SELECT transaction_timestamp()").fetchone()[0]
    grant = grant_standing_authorization(
        api,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
        request=StandingGrantRequest(
            site_id=scope.site_id,
            recipe_ranges=(RecipeRange("title_description_improvement", "1.0.0", "1.0.1"),),
            thresholds={"draft_patch": 0.8},
            weekly_volume_caps={"draft_patch": 1},
            weekly_total_cap=1,
            weekly_spend_cents=100,
            excluded_paths=(),
            starts_at=now,
            ends_at=now + timedelta(days=7),
            recovery_window_hours=24,
        ),
    )
    cycle = WeeklyCycle.for_window(
        WeeklySite(str(scope.tenant_id), str(scope.site_id), str(grant.id)), now.date()
    )
    assert (
        workflow.execute(
            "SELECT control.open_weekly_cycle(%s,%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                grant.id,
                cycle.week_start,
                cycle.cycle_id,
                str(uuid4()),
                identity_context["generation"],
            ),
        ).fetchone()[0]
        == "opened"
    )
    command_id = workflow.execute(
        "SELECT control.admit_weekly_observation(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            scope.tenant_id,
            scope.site_id,
            cycle.week_start,
            cycle.cycle_id,
            grant.id,
            identity_context["generation"],
            uuid4(),
            uuid4(),
            uuid4(),
        ),
    ).fetchone()[0]
    envelope = claim_outbox_batch(
        scheduler, tenant_id=scope.tenant_id, worker_key="worker.weekly-crawl", batch_size=1
    )[0]
    admitted = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admitted,
        WorkflowStartReceipt(admitted.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    command = CrawlSiteWorkflowInput(
        1,
        str(scope.tenant_id),
        str(scope.site_id),
        str(command_id),
        str(admitted.source_event_id),
        1,
        1,
    )
    fetcher = Fetcher(
        origin,
        after_robots=lambda: set_site_paused(
            api,
            session_token=identity_context["session_token"],
            site_id=scope.site_id,
            recovery_generation=identity_context["generation"],
            paused=True,
        ),
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _executor(tmp_path, fetcher).run(command, first_run_id=started.first_run_id)
    assert fetcher.calls == [f"{origin}/robots.txt"]
    assert (
        admin.execute(
            "SELECT count(*) FROM app.egress_operations WHERE tenant_id=%s AND site_id=%s",
            (scope.tenant_id, scope.site_id),
        ).fetchone()[0]
        == 0
    )
