import asyncio
import hashlib
import json
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.authorization import AuthorizationDenied
from signal_core.business_brain import FactCategory, FactProvenance, approve_fact, propose_fact
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_http import EgressHttpResult
from signal_core.keyword_ideas import expand_ideas
from signal_core.model_reasoning import BusinessBrainModelAdapter
from signal_core.seo_strategy_service import (
    StrategyConflict,
    StrategyUnavailable,
    refresh_strategy,
    strategy_call,
)
from signal_core.shared_egress import SharedEgressProvider
from signal_core.weekly_skill_ports import local_skill_ports
from signal_core.weekly_skills import WeeklySkills
from test_business_brain import owner
from test_business_brain_extraction import Credential, reset_provider_admission
from test_full_site_crawl import Fetcher, _command, _executor, _verify_origin
from test_seo_strategy import common
from test_shared_egress import authority
from test_shared_egress import request as gateway_request
from test_shared_egress import response as gateway_response

from tests.control_plane.measurement_support import bind_sources
from tests.control_plane.test_recipe_releases import release_manager as release_manager
from tests.control_plane.test_weekly_skills import (
    Recovery,
    open_skills,
    report,
    workflow_connection,
)
from tests.control_plane.test_weekly_skills import (
    skill_setup as skill_setup,
)


def seed(admin, api, identity, scheduler, workflow, scope, context, tmp_path):
    origin = _verify_origin(admin, identity, context, scope)
    command, run = _command(api, scheduler, workflow, scope, "synthetic-keyword-crawl")
    _executor(tmp_path, Fetcher(origin)).run(command, first_run_id=run)
    brain = {
        "session_token": context["session_token"],
        "current_recovery_generation": context["generation"],
        "site_id": scope.site_id,
    }
    propose_fact(
        api,
        **brain,
        category=FactCategory.AUDIENCE,
        statement="Founders are our audience.",
        provenance=FactProvenance("owner_statement"),
    )
    fact = admin.execute(
        "SELECT id FROM app.business_brain_facts WHERE tenant_id=%s", (scope.tenant_id,)
    ).fetchone()[0]
    approve_fact(api, **brain, fact_id=fact)
    bindings, page = bind_sources(admin, identity, scope, context)
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
            bindings["gsc"],
            page,
            Jsonb(["query", "page"]),
            Jsonb(
                [
                    {
                        "keys": [query, page],
                        "clicks": 1,
                        "impressions": 100,
                        "position": 12,
                        "ctr": 0.01,
                    }
                    for query in ["startup planning", "startup planning guide", "తెలుగు వార్తలు"]
                ]
            ),
            Jsonb({"complete": False, "missing_data": "unknown_not_zero"}),
            b"x" * 32,
            uuid4(),
        ),
    )


def test_registry_uses_topics_and_creates_only_capped_unaccepted_brief_proposals(
    admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path, skill_setup
):
    cycle = skill_setup()
    seed(admin, api, identity, scheduler, workflow, scopes[0], identity_context, tmp_path)
    open_skills(workflow, cycle)
    engine = WeeklySkills(
        workflow_connection, Recovery(), ports=local_skill_ports(workflow_connection)
    )
    asyncio.run(engine.run(cycle, "research"))
    stages = {s["stage"]: s for s in report(api, cycle, identity_context)["skill_stages"]}
    assert stages["strategy_rebuild"]["detail_code"] == "STRATEGY_RECORDED"
    assert stages["brief_proposals"]["detail_code"] == "BRIEFS_PROPOSED"
    assert stages["brief_proposals"]["units"] == 2
    view = strategy_call(api, **common(identity_context, scopes[0]), action="read")
    clusters = view["snapshot"]["payload"]["topics"]["clusters"]
    assert len(clusters) == 2
    assert sorted(len(cluster["members"]) for cluster in clusters) == [1, 2]
    assert "learning" in view["snapshot"]["payload"]
    assert not view["decisions"]
    for table in [
        "content_brief_acceptances",
        "candidate_recipe_review_decisions",
        "github_pr_operations",
        "sessions",
    ]:
        count = admin.execute(
            f"SELECT count(*) FROM app.{table} WHERE tenant_id=%s", (scopes[0].tenant_id,)
        ).fetchone()[0]
        assert count == (1 if table == "sessions" else 0)
    asyncio.run(engine.run(cycle, "research"))
    assert (
        admin.execute(
            "SELECT count(*) FROM app.content_briefs WHERE tenant_id=%s", (scopes[0].tenant_id,)
        ).fetchone()[0]
        == 2
    )


class IdeasFetcher:
    def __init__(self, mode):
        self.mode, self.calls = mode, []

    def request(self, outbound, *, policy):
        body = json.loads(outbound.body)
        self.calls.append(body)
        if self.mode == "timeout":
            raise TimeoutError("synthetic-model-timeout")
        cluster = json.loads(body["input"])["clusters"][0]["cluster_id"]
        output = {"ideas": [{"cluster_id": cluster, "query": "startup timeline"}]}
        if self.mode == "invalid":
            output["ideas"][0]["volume"] = 1000
        document = {
            "status": "completed",
            "id": "synthetic-topic-response",
            "model": "gpt-6-luna",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(output)}],
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        }
        raw = json.dumps(document).encode()
        return EgressHttpResult(
            1,
            outbound.url,
            outbound.url,
            "POST",
            "fetched",
            200,
            "application/json",
            (("content-type", "application/json"),),
            gateway_response(gateway_request("https://fixture.example.invalid")).resolved_address,
            raw,
            hashlib.sha256(raw).hexdigest(),
            len(raw),
            5,
        )


@pytest.mark.parametrize("mode", ["success", "invalid", "timeout", "cap", "oversize"])
def test_optional_model_uses_real_budget_shared_egress_receipts_and_retained_unknown_holds(
    admin,
    api,
    identity,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
    mode,
    monkeypatch,
):
    scope = scopes[0]
    seed(admin, api, identity, scheduler, workflow, scope, identity_context, tmp_path / "crawl")
    reset_provider_admission(admin)
    args = common(identity_context, scope)
    sid = UUID(refresh_strategy(api, **args)["snapshot_id"])
    store = EncryptedLocalArtifactStore(tmp_path / "model")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "synthetic-keyword-model",
        store,
        origin_override="https://api.openai.com",
        github_profile=True,
    )
    fetcher = IdeasFetcher(mode)
    refusals = []
    original_call = BusinessBrainModelAdapter._call

    async def inspect_call(self, *args, **kwargs):
        try:
            return await original_call(self, *args, **kwargs)
        except Exception as error:
            refusals.append(getattr(error, "code", type(error).__name__))
            raise

    monkeypatch.setattr(BusinessBrainModelAdapter, "_call", inspect_call)
    model = BusinessBrainModelAdapter(
        Credential(),
        SharedEgressProvider(
            crawl_admission,
            crawl_ingest,
            store,
            run,
            policy,
            fetcher,
            "worker.synthetic-keyword",
            OriginAdmissionPolicy(),
            b"k" * 32,
        ),
    )
    if mode == "cap":
        from signal_core.model_budget import PostgresModelBudget

        budget = PostgresModelBudget(
            api, identity_context["session_token"], identity_context["generation"], scope.site_id
        )
        assert budget.call("set_cap", 0) == "updated"
    if mode == "oversize":

        def oversized_packet(_):
            raise ValueError("Synthetic oversized input.")

        monkeypatch.setattr("signal_core.keyword_ideas.idea_packet", oversized_packet)

    def call():
        return asyncio.run(expand_ideas(api, **args, reasoner=model, snapshot_id=sid))

    if mode == "success":
        try:
            result = call()
        except StrategyUnavailable:
            pytest.fail(f"Synthetic model refused: {refusals}")
        assert result["state"] == "recorded"
        view = strategy_call(api, **args, action="read")
        topics = view["snapshot"]["payload"]["topics"]
        assert topics["ideas_status"] == "available"
        ideas = [i for c in topics["clusters"] for i in c["ideas"]]
        assert ideas[0]["label"] == "idea, no volume data" and ideas[0]["volume"] is None
        assert ideas[0]["evidence_ids"] and ideas[0]["strategy_item_id"]
        assert fetcher.calls[0]["model"] == "gpt-6-luna"
        assert fetcher.calls[0]["tools"] == [] and not fetcher.calls[0]["store"]
        assert fetcher.calls[0]["text"]["format"]["name"] == "signal_topic_ideas"
        assert (
            asyncio.run(
                expand_ideas(api, **args, reasoner=model, snapshot_id=UUID(view["snapshot"]["id"]))
            )["state"]
            == "replayed"
        )
        assert len(fetcher.calls) == 1
        assert not admin.execute(
            "SELECT * FROM app.content_briefs WHERE tenant_id=%s", (scope.tenant_id,)
        ).fetchall()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(
                "DELETE FROM app.keyword_idea_sets WHERE tenant_id=%s", (scope.tenant_id,)
            )
    else:
        with pytest.raises(StrategyUnavailable):
            call()
        with pytest.raises(StrategyUnavailable):
            call()
        assert len(fetcher.calls) == (0 if mode in {"cap", "oversize"} else 1), refusals
        assert not admin.execute(
            "SELECT * FROM app.keyword_idea_sets WHERE tenant_id=%s", (scope.tenant_id,)
        ).fetchall()
        if mode not in {"cap", "oversize"}:
            assert (
                admin.execute(
                    "SELECT reserved_micros FROM app.model_budget_calls WHERE tenant_id=%s",
                    (scope.tenant_id,),
                ).fetchone()[0]
                > 0
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT * FROM app.keyword_idea_sets")


@pytest.mark.parametrize(
    "negative", ["viewer", "analyst", "admin", "wrong_site", "wrong_tenant", "generation"]
)
def test_topics_owner_tenant_and_role_negatives_before_model_io(
    admin, api, scopes, identity_context, negative
):
    args = common(identity_context, scopes[0])
    if negative in {"viewer", "analyst", "admin"}:
        admin.execute(
            "UPDATE app.memberships SET role_key=%s WHERE id=%s",
            (negative, identity_context["membership_id"]),
        )
    else:
        owner(admin, identity_context)
    if negative in {"wrong_site", "wrong_tenant"}:
        args["site_id"] = scopes[1 if negative == "wrong_site" else 2].site_id
    if negative == "generation":
        args["generation"] = "synthetic-stale-generation"
    with pytest.raises(AuthorizationDenied):
        asyncio.run(expand_ideas(api, **args, reasoner=None, snapshot_id=uuid4()))
    with pytest.raises(AuthorizationDenied):
        strategy_call(api, **args, action="sources")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute(
            "SELECT control.keyword_topic_sources(%s,%s)", (scopes[0].tenant_id, scopes[0].site_id)
        )


def test_model_unavailable_and_receipt_forgery_are_explicit(admin, api, scopes, identity_context):
    owner(admin, identity_context)
    args = common(identity_context, scopes[0])
    sid = UUID(refresh_strategy(api, **args)["snapshot_id"])
    with pytest.raises(StrategyUnavailable):
        asyncio.run(expand_ideas(api, **args, reasoner=None, snapshot_id=sid))
    sources = strategy_call(api, **args, action="sources")
    assert (
        strategy_call(
            api,
            **args,
            action="record_ideas",
            args=(uuid4(), Jsonb(sources), Jsonb({"clusters": []}), b'{"ideas":[]}', b"x" * 32),
        )["state"]
        == "invalid"
    )
    propose_fact(
        api,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
        site_id=scopes[0].site_id,
        category=FactCategory.AUDIENCE,
        statement="Synthetic approved audience.",
        provenance=FactProvenance("owner_statement"),
    )
    fact = admin.execute(
        "SELECT id FROM app.business_brain_facts WHERE tenant_id=%s", (scopes[0].tenant_id,)
    ).fetchone()[0]
    approve_fact(
        api,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
        site_id=scopes[0].site_id,
        fact_id=fact,
    )
    with pytest.raises(StrategyConflict):
        asyncio.run(expand_ideas(api, **args, reasoner=None, snapshot_id=sid))
