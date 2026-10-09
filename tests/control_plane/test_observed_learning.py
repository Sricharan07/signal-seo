from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from signal_core.authorization import AuthorizationDenied
from signal_core.business_brain import FactCategory, FactProvenance, approve_fact, propose_fact
from signal_core.seo_strategy_service import decide_strategy, refresh_strategy, strategy_call
from test_seo_strategy import common

from tests.control_plane.measurement_support import bind_sources, clone_fixture_row, import_fixture
from tests.control_plane.test_full_site_crawl import Fetcher, _command, _executor, _verify_origin
from tests.control_plane.test_gsc_binding import session_args


def test_real_postgres_decay_proposal_and_current_binding(
    admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path
):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, run = _command(api, scheduler, workflow, scope, "synthetic-decay-crawl")
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
    page = origin + "/"
    bindings, origin_page = bind_sources(admin, identity, scope, identity_context)
    assert page == origin_page
    end = admin.execute(
        "SELECT (statement_timestamp() AT TIME ZONE 'America/Los_Angeles')::date-3"
    ).fetchone()[0]
    import_fixture(
        admin, scope, bindings, page, end - timedelta(days=55), end - timedelta(days=28), clicks=30
    )
    import_fixture(admin, scope, bindings, page, end - timedelta(days=27), end, clicks=10)
    args = common(identity_context, scope)
    first = refresh_strategy(api, **args)
    assert refresh_strategy(api, **args)["state"] == "replayed"
    result = strategy_call(api, **args, action="read")
    learning = result["snapshot"]["payload"]["learning"]
    assert learning["effectiveness"]["state"] == "unavailable"
    assert learning["decay"]["state"] == "partial"
    assert learning["decay"]["pages"][0]["declining"]
    item = next(
        i for i in result["snapshot"]["payload"]["strategy"]["items"] if i["kind"] == "decay"
    )
    assert item["action"]["payload"]["kind"] == "content_refresh"

    def count(table):
        return admin.execute(
            f"SELECT count(*) FROM app.{table} WHERE tenant_id=%s AND site_id=%s",
            (scope.tenant_id, scope.site_id),
        ).fetchone()[0]

    assert count("content_briefs") == 0
    assert not result["decisions"]
    accepted = decide_strategy(
        api,
        **args,
        snapshot_id=UUID(first["snapshot_id"]),
        item_id=UUID(item["id"]),
        decision="accepted",
    )
    assert accepted["target_kind"] == "brief"
    assert count("content_briefs") == 1
    assert count("content_brief_acceptances") == 0
    assert count("github_pr_operations") == 0
    assert identity.execute(
        "SELECT outcome FROM control.revoke_gsc_binding(%s,%s,%s,%s,%s)",
        (*session_args(identity_context, scope.site_id), bindings["gsc"], uuid4()),
    ).fetchone() == ("revoked",)
    assert strategy_call(api, **args, action="sources")["learning"]["generations"] == []
    refresh_strategy(api, **args)
    assert (
        strategy_call(api, **args, action="read")["snapshot"]["payload"]["learning"]["decay"][
            "state"
        ]
        == "unavailable"
    )


@pytest.mark.parametrize("negative", ["analyst", "wrong_site", "wrong_tenant", "generation"])
def test_learning_current_owner_scope_denied(admin, api, scopes, identity_context, negative):
    from test_business_brain import owner

    args = common(identity_context, scopes[0])
    if negative != "analyst":
        owner(admin, identity_context)
    if negative in {"wrong_site", "wrong_tenant"}:
        args["site_id"] = scopes[1 if negative == "wrong_site" else 2].site_id
    if negative == "generation":
        args["generation"] = "synthetic-stale-generation"
    with pytest.raises(AuthorizationDenied):
        strategy_call(api, **args, action="sources")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute(
            "SELECT control.seo_strategy_base_sources(%s,%s,%s)",
            session_args(identity_context, scopes[0].site_id),
        )


def test_partial_source_latest_generation_not_spliced(
    admin, api, identity, scopes, identity_context
):
    scope = scopes[0]
    _verify_origin(admin, identity, identity_context, scope)
    bindings, page = bind_sources(admin, identity, scope, identity_context)
    end = admin.execute(
        "SELECT (statement_timestamp() AT TIME ZONE 'America/Los_Angeles')::date-3"
    ).fetchone()[0]
    old, *_ = import_fixture(admin, scope, bindings, page, end - timedelta(days=55), end, clicks=30)
    row = admin.execute(
        "SELECT rows FROM app.gsc_import_generations WHERE id=%s", (old,)
    ).fetchone()[0]
    clone_fixture_row(
        admin,
        "gsc_import_generations",
        "id=%s",
        (old,),
        {
            "id": uuid4(),
            "rows": row[:-1],
            "imported_at": admin.execute("SELECT statement_timestamp() + interval '1 second'")
            .fetchone()[0]
            .isoformat(),
        },
    )
    args = common(identity_context, scope)
    refresh_strategy(api, **args)
    learning = strategy_call(api, **args, action="read")["snapshot"]["payload"]["learning"]
    assert learning["decay"]["pages"][0]["state"] == "partial"
    assert learning["decay"]["pages"][0]["prior_28_days"]["metrics"] is None
