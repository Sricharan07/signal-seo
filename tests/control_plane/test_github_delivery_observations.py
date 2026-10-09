import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
import rfc8785
from psycopg.types.json import Jsonb
from signal_core.ai_visibility_schedule import set_visibility_schedule
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_http import CrawlFetchUnavailable, EgressHttpResult
from signal_core.crawl_robots import persist_robots_snapshot
from signal_core.github_delivery_observation import (
    GitHubObservationUnavailable,
    observe_github_delivery,
    read_authenticated_github_delivery_observations,
)
from signal_core.live_verification import SharedLiveVerifier
from signal_core.shared_egress import SharedEgressProvider

from tests.control_plane.test_crawl_robots import artifact_key, robots_result
from tests.control_plane.test_github_pr_extension import _credential
from tests.control_plane.test_shared_egress import authority


class DeliveryDouble:
    def __init__(self, *, origin, body):
        self.calls = []
        self.live_body = body
        self.error = None
        now = datetime.now(UTC) - timedelta(minutes=5)
        self.documents = {
            "/app/installations/9342/access_tokens": {
                "token": "t" * 32,
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "permissions": {
                    "contents": "read",
                    "pull_requests": "read",
                    "checks": "read",
                    "deployments": "read",
                    "statuses": "read",
                    "metadata": "read",
                },
                "repository_selection": "selected",
                "repositories": [{"id": 245, "full_name": "SignalOwner/website"}],
            },
            "/repos/SignalOwner/website": {"id": 245, "full_name": "SignalOwner/website"},
            "/repos/SignalOwner/website/pulls/42": {
                "number": 42,
                "html_url": "https://github.com/SignalOwner/website/pull/42",
                "merged": True,
                "state": "closed",
                "merge_commit_sha": "b" * 40,
                "merged_at": now.isoformat(),
                "head": {"sha": "2" * 40, "repo": {"id": 245}},
                "base": {"ref": "main", "repo": {"id": 245}},
            },
            "/repos/SignalOwner/website/commits/" + "2" * 40 + "/check-runs": {
                "total_count": 1,
                "check_runs": [
                    {"id": 91, "head_sha": "2" * 40, "status": "completed", "conclusion": "success"}
                ],
            },
            "/repos/SignalOwner/website/commits/" + "2" * 40 + "/status": {
                "sha": "2" * 40,
                "total_count": 0,
                "statuses": [],
            },
            "/repos/SignalOwner/website/git/commits/" + "b" * 40: {
                "sha": "b" * 40,
                "tree": {"sha": "1" * 40},
            },
            "/repos/SignalOwner/website/deployments": [
                {
                    "id": 81,
                    "sha": "b" * 40,
                    "environment": "production",
                    "production_environment": True,
                    "transient_environment": False,
                    "creator": {"id": 56},
                    "created_at": (now + timedelta(seconds=1)).isoformat(),
                }
            ],
            "/repos/SignalOwner/website/deployments/81/statuses": [
                {
                    "id": 82,
                    "state": "success",
                    "environment": "production",
                    "environment_url": origin + "/",
                    "deployment_url": "https://api.github.com/repos/SignalOwner/website/deployments/81",
                    "creator": {"id": 56},
                    "created_at": (now + timedelta(seconds=2)).isoformat(),
                }
            ],
        }

    def request(self, outbound, *, policy):
        self.calls.append(outbound)
        if outbound.accepted_media_types[0] == "text/html":
            if self.error:
                raise self.error
            body, media, status = self.live_body, "text/html", 200
        else:
            body, media, status = (
                json.dumps(self.documents[urlsplit(outbound.url).path]).encode(),
                "application/json",
                201 if outbound.method == "POST" else 200,
            )
        return EgressHttpResult(
            1,
            outbound.url,
            outbound.url,
            outbound.method,
            "fetched",
            status,
            media,
            (("content-type", media),),
            "8.8.8.8",
            body,
            hashlib.sha256(body).hexdigest(),
            len(body),
            5,
        )


async def exercise_delivery_observations(
    admin,
    api,
    identity,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scope,
    context,
    operation_id,
    canonical,
    tmp_path,
):
    manifest = json.loads(canonical)
    origin = manifest["evidence"]["site_origin"]
    source = b'<html><head></head><body><h1>Signal Guide</h1><img src="guide.png"></body></html>'
    result = source.replace(b'<img src="guide.png">', b'<img src="guide.png" alt="Signal Guide">')
    store = EncryptedLocalArtifactStore(tmp_path / "delivery-artifacts")
    github_run, github_policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "delivery-github-" + uuid4().hex,
        store,
        origin_override="https://api.github.com",
        github_profile=True,
    )
    live_run, live_policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "delivery-live-" + uuid4().hex,
        store,
        origin_override=origin,
        verification_profile=True,
    )
    double = DeliveryDouble(origin=origin, body=result)
    admission_policy = OriginAdmissionPolicy()
    github = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        github_run,
        github_policy,
        double,
        "delivery.github",
        admission_policy,
        None,
        "connector",
    )
    live = SharedLiveVerifier(
        crawl_admission,
        crawl_ingest,
        store,
        live_run,
        live_policy,
        double,
        "delivery.live",
        admission_policy,
    )
    credential, bao = _credential()
    token_hash = hashlib.sha256(context["session_token"].encode()).digest()
    args = (token_hash, scope.site_id, context["generation"])

    async def observe(attempt, *, robots_denied=False):
        fresh_run, fresh_policy, _ = authority(
            api,
            scheduler,
            workflow,
            crawl_admission,
            crawl_ingest,
            scope,
            "delivery-attempt-" + uuid4().hex,
            store,
            origin_override=origin,
            verification_profile=True,
        )
        current_live = replace(live, run=fresh_run, policy=fresh_policy)
        if robots_denied:
            now = datetime.now(UTC)
            persist_robots_snapshot(
                crawl_ingest,
                store,
                fresh_run,
                fresh_policy,
                robots_result(
                    body=b"User-agent: *\nDisallow: /\n",
                    origin=origin,
                    robots_url=origin + "/robots.txt",
                    final_url=origin + "/robots.txt",
                ),
                snapshot_id=uuid4(),
                fetched_at=now,
                recorded_at=now,
                network_profile_sha256="cd" * 32,
                artifact_key=artifact_key(),
                retain_until=now + timedelta(days=1),
            )
            current_live = replace(current_live, artifact_key=artifact_key())
        return await observe_github_delivery(
            identity,
            session_token=context["session_token"],
            site_id=scope.site_id,
            current_recovery_generation=context["generation"],
            operation_id=operation_id,
            attempt_id=attempt,
            environment="production",
            trusted_deployment_actor_id=56,
            credential=credential,
            github_egress=github,
            live_verifier=current_live,
            openbao_transport=bao,
        )

    attempt = uuid4()
    receipt = await observe(attempt)
    assert receipt["outcome"] == "verified" and receipt["delivery_certified"] is False
    assert receipt["provider"]["deployment"]["sha"] == "b" * 40
    assert receipt["live"]["fetched_sha256"] == manifest["result_sha256"]
    # Check the exact database deadline before any slower downstream qualification.
    assert admin.execute(
        "SELECT next_observe_at-created_at FROM app.github_delivery_attempts WHERE id=%s",
        (attempt,),
    ).fetchone() == (timedelta(seconds=30),)
    before = len(double.calls)
    with admin.transaction():
        # transaction_timestamp stays fixed for this assertion, even under host load.
        admin.execute(
            "UPDATE app.github_delivery_attempts SET next_observe_at=transaction_timestamp()"
            "+interval '30 seconds' WHERE id=%s",
            (attempt,),
        )
        assert admin.execute(
            "SELECT * FROM control.begin_github_delivery_observation(%s,%s,%s,%s,%s,%s,%s)",
            (*args, operation_id, uuid4(), "production", 56),
        ).fetchone() == (None, None, "observation_backoff")
    assert len(double.calls) == before
    question_set = uuid4()
    assert (
        crawl_ingest.execute(
            "SELECT control.record_ai_visibility_question_set(%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                question_set,
                UUID(manifest["evidence"]["manifest_id"]),
                Jsonb(
                    [
                        {
                            "id": str(uuid4()),
                            "question": "What does this site offer?",
                            "source_kind": "owner",
                            "source_evidence_id": None,
                        }
                    ]
                ),
            ),
        ).fetchone()[0]
        == "recorded"
    )
    set_visibility_schedule(
        api,
        session_token=context["session_token"],
        site_id=scope.site_id,
        generation=context["generation"],
        request_id=uuid4(),
        enabled=True,
        monthly_cap_micros=0,
    )
    run = uuid4()
    observed = workflow.execute(
        "SELECT control.open_ai_visibility_run(%s,%s,%s,%s)",
        (scope.tenant_id, scope.site_id, run, context["generation"]),
    ).fetchone()[0]
    assert observed["questions"]
    assert observed["reason"] is None and observed["observed_changes"] == [str(operation_id)]
    assert (
        workflow.execute(
            "SELECT control.open_ai_visibility_run(%s,%s,%s,%s)",
            (scope.tenant_id, scope.site_id, run, context["generation"]),
        ).fetchone()[0]
        == observed
    )
    deferred = workflow.execute(
        "SELECT control.open_ai_visibility_run(%s,%s,%s,%s)",
        (scope.tenant_id, scope.site_id, uuid4(), context["generation"]),
    ).fetchone()[0]
    assert deferred["reason"] == "CADENCE_NOT_DUE" and deferred["observed_changes"] == []
    for evidence in receipt["provider_evidence"]:
        assert admin.execute(
            "SELECT egress_profile,method FROM app.egress_operations WHERE id=%s",
            (UUID(evidence["egress_operation_id"]),),
        ).fetchone() == ("github_rest", "GET")
    assert admin.execute(
        "SELECT egress_profile,method,credentialed FROM app.egress_operations WHERE id=%s",
        (UUID(receipt["live_egress_operation_id"]),),
    ).fetchone() == ("crawl_page", "GET", False)
    before = len(double.calls)
    assert await observe(attempt) == receipt
    assert len(double.calls) == before
    records = read_authenticated_github_delivery_observations(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
    )
    assert records[0].receipt == receipt and records[0].state == "completed"
    assert records[0].receipt_sha256 == hashlib.sha256(rfc8785.dumps(receipt)).hexdigest()
    from tests.control_plane.test_indexnow import exercise_indexnow_queue

    await exercise_indexnow_queue(admin, identity, scope, context, operation_id, tmp_path)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute("UPDATE app.github_delivery_receipts SET canonical_receipt='{}'")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute("DELETE FROM app.github_delivery_receipts WHERE attempt_id=%s", (attempt,))
    bad = rfc8785.dumps({**receipt, "reason": "FORGED"})
    assert identity.execute(
        "SELECT control.finish_github_delivery_observation(%s,%s,%s,%s,%s,%s)",
        (*args, attempt, bad, hashlib.sha256(bad).digest()),
    ).fetchone() == ("receipt_conflict",)
    for scenario in ("stale", "absent", "timeout", "wrong_commit"):
        admin.execute(
            "UPDATE app.github_delivery_attempts SET next_observe_at=created_at "
            "WHERE operation_id=%s",
            (operation_id,),
        )
        double.live_body = (
            source
            if scenario == "stale"
            else b"<title>Other page</title>"
            if scenario == "absent"
            else result
        )
        double.error = CrawlFetchUnavailable("timeout") if scenario == "timeout" else None
        double.documents["/repos/SignalOwner/website/deployments"][0]["sha"] = (
            "d" * 40 if scenario == "wrong_commit" else "b" * 40
        )
        current = await observe(uuid4())
        assert current["outcome"] == "inconclusive"
        assert (
            current["reason"]
            == {
                "stale": "EC_077_STALE_PAGE",
                "absent": "EC_123_POSTCONDITION_MISMATCH",
                "timeout": "LIVE_FETCH_TIMEOUT",
                "wrong_commit": "EC_123_DEPLOYMENT_COMMIT_MISMATCH",
            }[scenario]
        )
    admin.execute(
        "UPDATE app.github_delivery_attempts SET next_observe_at=created_at WHERE operation_id=%s",
        (operation_id,),
    )
    double.error = None
    double.documents["/repos/SignalOwner/website/deployments"][0]["sha"] = "b" * 40
    before_live = sum(call.accepted_media_types[0] == "text/html" for call in double.calls)
    denied = await observe(uuid4(), robots_denied=True)
    assert denied["outcome"] == "inconclusive" and denied["reason"] == "LIVE_ROBOTS_DENIED"
    assert sum(call.accepted_media_types[0] == "text/html" for call in double.calls) == before_live
    admin.execute(
        "UPDATE app.github_delivery_attempts SET next_observe_at=created_at WHERE operation_id=%s",
        (operation_id,),
    )
    lost = uuid4()
    begun = identity.execute(
        "SELECT * FROM control.begin_github_delivery_observation(%s,%s,%s,%s,%s,%s,%s)",
        (*args, operation_id, lost, "production", 56),
    ).fetchone()
    assert begun[2] == "dispatching"
    # A complete-looking forged success without current live evidence cannot be committed.
    for mutation in (
        {"live": None},
        {
            "provider": {
                **receipt["provider"],
                "deployment": {**receipt["provider"]["deployment"], "environment_url": None},
            }
        },
    ):
        forged = rfc8785.dumps({**receipt, **mutation, "attempt_id": str(lost)})
        assert identity.execute(
            "SELECT control.finish_github_delivery_observation(%s,%s,%s,%s,%s,%s)",
            (*args, lost, forged, hashlib.sha256(forged).digest()),
        ).fetchone()[0] in (
            "provider_evidence_invalid",
            "deployment_identity_invalid",
            "live_evidence_invalid",
        )
    with pytest.raises(GitHubObservationUnavailable, match="outcome_unknown"):
        await observe(lost)
    assert len(double.calls) >= before
    admin.execute(
        "UPDATE app.github_delivery_attempts SET expires_at=created_at+interval '1 millisecond',"
        "created_at=created_at-interval '10 seconds' WHERE id=%s",
        (lost,),
    )
    assert identity.execute(
        "SELECT control.github_delivery_read_permit(%s,%s,%s,%s)", (*args, lost)
    ).fetchone() == ("observation_stale",)
    admin.execute(
        "UPDATE app.sessions SET revoked_at=now() WHERE id=%s", (context["tenant_session_id"],)
    )
    assert identity.execute(
        "SELECT control.github_delivery_read_permit(%s,%s,%s,%s)", (*args, lost)
    ).fetchone() == ("invalid_session",)
    admin.execute(
        "UPDATE app.sessions SET revoked_at=NULL WHERE id=%s", (context["tenant_session_id"],)
    )


@pytest.mark.parametrize("table", ["github_delivery_attempts", "github_delivery_receipts"])
def test_delivery_tables_are_forced_rls_and_unavailable_to_runtime_roles(admin, api, table):
    assert admin.execute(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
        ("app." + table,),
    ).fetchone() == (True, True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT count(*) FROM app." + table)
