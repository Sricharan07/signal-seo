from hashlib import sha256
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.authorization import AuthorizationDenied
from signal_core.business_brain import (
    FactCategory,
    FactProvenance,
    approve_fact,
    propose_fact,
    remove_fact,
)
from signal_core.crawl_audit import analyze_crawl_manifest
from signal_core.decision_contracts import canonical_json
from signal_core.seo_strategy import build_snapshot
from signal_core.seo_strategy_service import (
    StrategyConflict,
    StrategyUnavailable,
    decide_strategy,
    refresh_strategy,
    strategy_call,
)
from test_business_brain import owner
from test_full_site_crawl import Fetcher, _command, _executor, _verify_origin
from test_gsc_binding import begin, session_args


def common(context, scope):
    return {
        "session_token": context["session_token"],
        "generation": context["generation"],
        "site_id": scope.site_id,
    }


def test_partial_snapshot_replay_versioning_immutable_and_dismiss(
    admin, api, scopes, identity_context
):
    owner(admin, identity_context)
    args = common(identity_context, scopes[0])
    first = refresh_strategy(api, **args)
    assert first["state"] == "recorded"
    assert refresh_strategy(api, **args)["state"] == "replayed"
    view = strategy_call(api, **args, action="read")
    assert view["snapshot"]["version"] == 1 and not view["snapshot"]["payload"]["headline"]
    assert {s["source"] for s in view["snapshot"]["payload"]["unavailable"]} >= {
        "gsc",
        "bing",
        "ai_visibility",
        "dataforseo",
    }
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT * FROM app.seo_strategy_snapshots")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute("UPDATE app.seo_strategy_snapshots SET version=2")


@pytest.mark.parametrize("negative", ["analyst", "wrong_site", "wrong_tenant", "generation"])
def test_existing_owner_authority_denies_negatives(admin, api, scopes, identity_context, negative):
    args = common(identity_context, scopes[0])
    if negative != "analyst":
        owner(admin, identity_context)
    if negative in {"wrong_site", "wrong_tenant"}:
        args["site_id"] = scopes[1 if negative == "wrong_site" else 2].site_id
    if negative == "generation":
        args["generation"] = "stale-generation"
    with pytest.raises(AuthorizationDenied):
        refresh_strategy(api, **args)
    with pytest.raises(AuthorizationDenied):
        strategy_call(api, **args, action="read")
    with pytest.raises(AuthorizationDenied):
        decide_strategy(api, **args, snapshot_id=uuid4(), item_id=uuid4(), decision="accepted")


def test_real_crawl_baseline_findings_stale_snapshot_and_unsupported_accept(
    admin, api, identity, scheduler, workflow, crawl_ingest, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, run = _command(api, scheduler, workflow, scope, "synthetic-baseline-crawl")
    manifest = _executor(tmp_path, Fetcher(origin)).run(command, first_run_id=run)
    analyze_crawl_manifest(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        manifest_id=UUID(manifest.manifest_id),
    )
    args = common(identity_context, scope)
    sources = strategy_call(api, **args, action="sources")
    snapshot = build_snapshot(sources)
    assert snapshot["headline"]["fetched_count"]["value"] == 1
    assert snapshot["pages"][0]["url"] == origin + "/"
    assert snapshot["strategy"]["items"]
    refresh_strategy(api, **args)
    view = strategy_call(api, **args, action="read")
    item = UUID(view["snapshot"]["payload"]["strategy"]["items"][0]["id"])
    sid = UUID(view["snapshot"]["id"])
    with pytest.raises(StrategyUnavailable):
        decide_strategy(api, **args, snapshot_id=sid, item_id=item, decision="accepted")
    assert (
        decide_strategy(api, **args, snapshot_id=sid, item_id=item, decision="dismissed")["state"]
        == "dismissed"
    )
    assert (
        decide_strategy(api, **args, snapshot_id=sid, item_id=item, decision="dismissed")["state"]
        == "replayed"
    )
    with pytest.raises(StrategyConflict):
        decide_strategy(api, **args, snapshot_id=sid, item_id=item, decision="accepted")
    with pytest.raises(StrategyConflict):
        decide_strategy(api, **args, snapshot_id=uuid4(), item_id=item, decision="dismissed")
    assert not admin.execute(
        "SELECT id FROM app.content_briefs WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchall()
    altered = {**snapshot, "sources": {**sources, "inbox": [{"id": str(uuid4())}]}}
    assert (
        strategy_call(api, **args, action="record", args=(uuid4(), canonical_json(altered)))[
            "state"
        ]
        == "stale"
    )


def test_query_acceptance_is_exact_unaccepted_brief_atomic_replay_and_removed_fact_failure(
    admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, run = _command(api, scheduler, workflow, scope, "synthetic-baseline-content")
    _executor(tmp_path, Fetcher(origin)).run(command, first_run_id=run)
    brain = {
        "session_token": identity_context["session_token"],
        "current_recovery_generation": identity_context["generation"],
        "site_id": scope.site_id,
    }
    propose_fact(
        api,
        **brain,
        category=FactCategory.AUDIENCE,
        statement="Founders are our audience.",
        provenance=FactProvenance("owner_statement"),
    )
    fid = admin.execute(
        "SELECT id FROM app.business_brain_facts WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone()[0]
    approve_fact(api, **brain, fact_id=fid)
    aid, bid = uuid4(), uuid4()
    assert (
        begin(identity, identity_context, scope.site_id, aid, b"synthetic-baseline-state")
        == "created"
    )
    base = (*session_args(identity_context, scope.site_id), aid)
    assert identity.execute(
        "SELECT outcome FROM control.consume_gsc_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (
            *base,
            sha256(b"synthetic-baseline-state").digest(),
            "https://signal.example/oauth/gsc/callback",
        ),
    ).fetchone() == ("consumed",)
    assert identity.execute(
        "SELECT control.stage_gsc_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (
            *base,
            f"secret://gsc/{aid}",
            Jsonb(
                [{"resource_name": origin + "/", "property_type": "url_prefix", "eligible": True}]
            ),
        ),
    ).fetchone() == ("staged",)
    assert identity.execute(
        "SELECT control.confirm_gsc_binding(%s,%s,%s,%s,%s,%s,%s)",
        (*base, bid, uuid4(), origin + "/"),
    ).fetchone() == ("bound",)
    # Projection fixtures are synthetic database evidence, not provider success.
    admin.execute(
        "INSERT INTO app.gsc_import_generations "
        "(tenant_id,site_id,id,binding_id,property_resource_name,search_type,dimensions,"
        "start_date,end_date,data_state,aggregation_type,rows,coverage,response_sha256,"
        "egress_operation_id) VALUES(%s,%s,%s,%s,%s,'web',%s,'2026-09-01','2026-09-28',"
        "'final','byPage',%s,%s,%s,%s)",
        (
            scope.tenant_id,
            scope.site_id,
            uuid4(),
            bid,
            origin + "/",
            Jsonb(["query", "page"]),
            Jsonb(
                [
                    {
                        "keys": ["startup planning", origin + "/"],
                        "clicks": 1,
                        "impressions": 100,
                        "ctr": 0.01,
                        "position": 11,
                    }
                ]
            ),
            Jsonb({"complete": False, "missing_data": "unknown_not_zero"}),
            b"x" * 32,
            uuid4(),
        ),
    )
    args = common(identity_context, scope)
    refresh_strategy(api, **args)
    view = strategy_call(api, **args, action="read")
    sid = UUID(view["snapshot"]["id"])
    item = next(
        i for i in view["snapshot"]["payload"]["strategy"]["items"] if i["kind"] == "content"
    )
    action = {"snapshot_id": sid, "item_id": UUID(item["id"]), "decision": "accepted"}
    accepted = decide_strategy(api, **args, **action)
    assert accepted["state"] == "accepted" and accepted["target_kind"] == "brief"
    assert decide_strategy(api, **args, **action)["target_id"] == accepted["target_id"]
    assert (
        admin.execute(
            "SELECT count(*) FROM app.content_briefs WHERE tenant_id=%s", (scope.tenant_id,)
        ).fetchone()[0]
        == 1
    )
    assert not admin.execute(
        "SELECT * FROM app.content_brief_acceptances WHERE tenant_id=%s", (scope.tenant_id,)
    ).fetchall()
    assert (
        admin.execute(
            "SELECT payload->>'query' FROM app.content_briefs WHERE tenant_id=%s",
            (scope.tenant_id,),
        ).fetchone()[0]
        == "startup planning"
    )
    # New inventory creates a version, but cannot duplicate the same accepted item.
    assert refresh_strategy(api, **args)["state"] == "recorded"
    current = strategy_call(api, **args, action="read")
    assert current["snapshot"]["version"] == 2 and current["decisions"][0]["decision"] == "accepted"
    assert (
        decide_strategy(api, **args, **{**action, "snapshot_id": UUID(current["snapshot"]["id"])})[
            "state"
        ]
        == "replayed"
    )
    assert strategy_call(api, **args, action="read", args=(sid,))["snapshot"]["version"] == 1
    with pytest.raises(StrategyConflict):
        decide_strategy(api, **args, **action)
    remove_fact(api, **brain, fact_id=fid)
    assert refresh_strategy(api, **args)["state"] == "recorded"
    current = strategy_call(api, **args, action="read")
    assert current["snapshot"]["payload"]["strategy"]["items"][0]["action"] is None
