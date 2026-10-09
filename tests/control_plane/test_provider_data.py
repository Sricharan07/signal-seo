"""Dual spend ledgers and provider ports on disposable real PostgreSQL."""

import asyncio
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_http import CrawlFetchUnavailable
from signal_core.dataforseo import DataForSeoQuery
from signal_core.dataforseo_service import StrategyDataForSeoResearch
from signal_core.seo_strategy_service import rebuild_strategy, strategy_call
from signal_core.strategy_provider_egress import StrategyProviderEgressFactory
from signal_core.weekly_skill_ports import ImportSkillPort, StrategySkillPort
from signal_core.weekly_skills import SkillPermit

from tests.connectors.test_seo_strategy_domain import packet
from tests.control_plane.test_bing_pages import SyntheticSecrets
from tests.control_plane.test_bing_pages import pages_context as pages_context
from tests.control_plane.test_dataforseo import Credentials, configure
from tests.control_plane.test_recipe_releases import release_manager as release_manager
from tests.control_plane.test_shared_egress import response
from tests.control_plane.test_weekly_skills import admit, open_skills, workflow_connection
from tests.control_plane.test_weekly_skills import skill_setup as skill_setup


class PaidFetcher:
    def __init__(self, *, fail=False, cost=None):
        self.calls, self.fail, self.cost = [], fail, cost

    def request(self, request, *, policy):
        self.calls.append(request)
        if request.url.endswith("/robots.txt"):
            body = b"User-agent: *\nAllow: /\n"
            media = "text/plain"
        else:
            if self.fail:
                raise CrawlFetchUnavailable()
            data = json.loads(request.body)[0]
            if "keywords" in data:
                result = {**data, "keyword": data["keywords"][0], "search_volume": 700}
                cost = 0.09
            elif "keyword" in data:
                result = {
                    **data,
                    "items_count": 1,
                    "items": [
                        {
                            "type": "organic",
                            "rank_group": 1,
                            "domain": "competitor.example",
                            "url": "https://competitor.example/",
                        }
                    ],
                }
                cost = 0.002
            else:
                result = {**data, "backlinks": 12, "referring_domains": 3, "rank": 9}
                cost = 0.024036
            cost = self.cost if self.cost is not None else cost
            body = json.dumps(
                {
                    "status_code": 20000,
                    "cost": cost,
                    "tasks_count": 1,
                    "tasks_error": 0,
                    "tasks": [
                        {
                            "id": "synthetic-paid-task",
                            "status_code": 20000,
                            "cost": cost,
                            "path": request.url.split(".com/")[1].split("/"),
                            "data": data,
                            "result_count": 1,
                            "result": [result],
                        }
                    ],
                }
            ).encode()
            media = "application/json"
        return replace(
            response(SimpleNamespace(http=request)),
            body=body,
            media_type=media,
            body_sha256=hashlib.sha256(body).hexdigest(),
            decoded_bytes=len(body),
        )


@pytest.fixture
def paid(
    admin, api, workflow, scopes, identity_context, skill_setup, tmp_path, monkeypatch, request
):
    cycle = skill_setup(**getattr(request, "param", {}))
    scope = scopes[0]
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (identity_context["identity_session_id"],),
    )
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE id=%s",
        (identity_context["tenant_session_id"],),
    )
    credential = uuid4()
    assert configure(api, identity_context, scope.site_id, "prepare", credential)[2] == "configured"
    assert (
        configure(api, identity_context, scope.site_id, "activate", credential)[2] == "configured"
    )
    fetcher = PaidFetcher()
    monkeypatch.setattr("signal_core.strategy_provider_egress.PinnedHttpFetcher", lambda _: fetcher)
    factory = StrategyProviderEgressFactory(
        workflow_connection,
        EncryptedLocalArtifactStore(tmp_path / "paid"),
        ArtifactEncryptionKey("artifact-key:v1:paid-test", b"P" * 32),
        None,
    )
    research = StrategyDataForSeoResearch(workflow_connection, Credentials(), factory, 2840, "en")
    delay = admin.execute(
        "SELECT extract(epoch FROM (greatest(next_allowed_at,degraded_until)-clock_timestamp())) "
        "FROM control.origin_buckets WHERE origin='https://api.dataforseo.com'"
    ).fetchone()
    if delay and delay[0] > 0:
        time.sleep(float(delay[0]) + 0.05)
    return cycle, credential, fetcher, research


def common(context, scope):
    return dict(
        session_token=context["session_token"],
        generation=context["generation"],
        site_id=scope.site_id,
    )


def run(research, context, scope):
    sources = packet(False)
    sources["content"]["records"] = [
        {"id": str(uuid4()), "title": "widgets", "url": "https://example.invalid/"}
    ]
    asyncio.run(research.run(sources, **common(context, scope)))


def test_owner_paid_volume_serp_backlinks_evidence_and_monthly_replay(
    paid, admin, api, scopes, identity_context
):
    from dataclasses import replace

    _, _, fetcher, research = paid
    research = replace(research, include_backlinks=True)
    run(research, identity_context, scopes[0])
    assert len(fetcher.calls) == 6
    args = common(identity_context, scopes[0])
    asyncio.run(rebuild_strategy(api, **args, research=research))
    sources = strategy_call(api, **args, action="sources")
    assert {r["result"]["kind"] for r in sources["dataforseo"]["records"]} == {
        "volume",
        "serp",
        "backlinks",
    }
    view = strategy_call(api, **args, action="read")["snapshot"]["payload"]
    assert {r["kind"] for r in view["evidence"].values()} >= {
        "dataforseo_volume",
        "dataforseo_serp",
        "dataforseo_backlinks",
    }
    assert any(i["title"] == "Explore topic: widgets" for i in view["strategy"]["items"])
    before = len(fetcher.calls)
    run(research, identity_context, scopes[0])
    assert len(fetcher.calls) == before
    usage = admin.execute(
        "SELECT spend_cents,total_count FROM app.autonomy_weekly_usage WHERE site_id=%s",
        (scopes[0].site_id,),
    ).fetchone()
    assert usage == (13, 3)  # 9 + 1 + 3 cents, no refund or replay charge.


@pytest.mark.parametrize(
    "cap,paid", [("monthly", {}), ("standing", {"spend": 0})], indirect=["paid"]
)
def test_both_caps_stop_before_secret_or_http_and_roll_back_denied_reservation(
    paid, admin, api, scopes, identity_context, skill_setup, cap
):
    _, _, fetcher, research = paid
    if cap == "monthly":
        configure(api, identity_context, scopes[0].site_id, "cap", cap=0)
    run(research, identity_context, scopes[0])
    assert not fetcher.calls
    assert not admin.execute(
        "SELECT id FROM app.dataforseo_calls WHERE site_id=%s", (scopes[0].site_id,)
    ).fetchall()
    assert not admin.execute(
        "SELECT operation_id FROM app.autonomy_reservations WHERE site_id=%s", (scopes[0].site_id,)
    ).fetchall()
    reason = strategy_call(api, **common(identity_context, scopes[0]), action="sources")[
        "dataforseo"
    ]["reason"]
    assert "Not implemented" not in reason


def test_unknown_outcome_held_on_repeat_and_credential_rotation(
    paid, admin, api, scopes, identity_context
):
    _, _, fetcher, research = paid
    fetcher.fail = True
    run(research, identity_context, scopes[0])
    assert len(fetcher.calls) == 2
    run(research, identity_context, scopes[0])
    replacement = uuid4()
    configure(api, identity_context, scopes[0].site_id, "prepare", replacement)
    configure(api, identity_context, scopes[0].site_id, "activate", replacement)
    run(research, identity_context, scopes[0])
    assert len(fetcher.calls) == 2
    assert admin.execute(
        "SELECT count(*) FROM app.dataforseo_calls WHERE site_id=%s", (scopes[0].site_id,)
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT spend_cents FROM app.autonomy_weekly_usage WHERE site_id=%s", (scopes[0].site_id,)
    ).fetchone() == (9,)
    assert (
        "held"
        in strategy_call(api, **common(identity_context, scopes[0]), action="sources")[
            "dataforseo"
        ]["reason"]
    )


@pytest.mark.parametrize("paid", [{"spend": 15}], indirect=True)
def test_reported_overrun_occupies_standing_ledger_and_stops_next_call(
    paid, admin, scopes, identity_context, skill_setup
):
    _, _, fetcher, research = paid
    fetcher.cost = 0.3
    run(research, identity_context, scopes[0])
    assert len(fetcher.calls) == 2
    assert admin.execute(
        "SELECT spend_cents FROM app.autonomy_weekly_usage WHERE site_id=%s", (scopes[0].site_id,)
    ).fetchone() == (30,)


@pytest.mark.parametrize(
    "negative", ["stale_mfa", "generation", "site", "tenant", "role", "paused"]
)
def test_paid_authority_denies_without_credential_or_dispatch(
    paid, admin, api, scopes, identity_context, negative
):
    from signal_core.weekly_control import set_site_paused

    _, _, fetcher, research = paid
    args = common(identity_context, scopes[0])
    if negative == "stale_mfa":
        admin.execute(
            "UPDATE app.sessions SET auth_time=clock_timestamp()-interval '6 minutes' WHERE id=%s",
            (identity_context["tenant_session_id"],),
        )
    elif negative == "generation":
        args["generation"] = "stale-generation"
    elif negative in {"site", "tenant"}:
        args["site_id"] = scopes[1 if negative == "site" else 2].site_id
    elif negative == "role":
        admin.execute(
            "UPDATE app.memberships SET role_key='analyst' WHERE id=%s",
            (identity_context["membership_id"],),
        )
    else:
        set_site_paused(
            api,
            session_token=identity_context["session_token"],
            recovery_generation=identity_context["generation"],
            site_id=scopes[0].site_id,
            paused=True,
        )
    sources = packet(False)
    sources["content"]["records"] = [
        {"id": str(uuid4()), "title": "widgets", "url": "https://example.invalid/"}
    ]
    asyncio.run(research.run(sources, **args))
    assert not fetcher.calls
    assert not admin.execute(
        "SELECT id FROM app.dataforseo_calls WHERE site_id=%s", (scopes[0].site_id,)
    ).fetchall()


def test_weekly_strategy_uses_same_paid_research_and_closed_ports(
    paid, admin, workflow, scopes, identity_context, monkeypatch
):
    cycle, _, fetcher, research = paid
    open_skills(workflow, cycle)
    admitted, handle = admit(workflow, cycle, "strategy_rebuild")
    assert admitted["state"] == "started"
    permit = SkillPermit(
        scopes[0].tenant_id,
        scopes[0].site_id,
        UUID(cycle.cycle_id),
        "strategy_rebuild",
        identity_context["generation"],
        handle,
        tuple(admitted["plan"]),
        cycle.week_start,
    )

    class ResearchWithSubject:
        async def run(self, sources, **args):
            sources = {
                **sources,
                "content": {"reason": None, "records": [{"id": str(uuid4()), "title": "widgets"}]},
            }
            await research.run(sources, **args)

    # Synthetic subjects supplement real authority, ledgers, egress and snapshot ports.
    result = asyncio.run(
        StrategySkillPort(workflow_connection, research=ResearchWithSubject()).run(
            permit, lambda: None
        )
    )
    assert result.outcome == "completed" and len(fetcher.calls) == 4
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        workflow.execute(
            "SELECT control.reserve_dataforseo(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                scopes[0].tenant_id,
                scopes[0].site_id,
                uuid4(),
                paid[1],
                "volume",
                DataForSeoQuery("volume", "widgets", 2840, "en").endpoint,
                b"x" * 32,
                Jsonb({}),
                90000,
            ),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        workflow.execute("SELECT * FROM app.strategy_provider_requests")


def test_weekly_bing_import_requests_site_and_pages_with_separate_provenance(
    skill_setup, pages_context, workflow, api, identity_context, admin
):
    cycle = skill_setup()
    scope, _, _, fetcher, gateway = pages_context
    open_skills(workflow, cycle)
    admitted, handle = admit(workflow, cycle, "import_bing")
    assert admitted["state"] == "started"
    permit = SkillPermit(
        scope.tenant_id,
        scope.site_id,
        UUID(cycle.cycle_id),
        "import_bing",
        identity_context["generation"],
        handle,
        tuple(admitted["plan"]),
        cycle.week_start,
    )
    result = asyncio.run(
        ImportSkillPort(workflow_connection, gateway, SyntheticSecrets(), "bing").run(
            permit, lambda: None
        )
    )
    assert result.outcome == "completed" and len(result.evidence_refs) == 2
    assert any("GetPageStats?" in r.url for r in fetcher.calls)
    assert any("GetRankAndTrafficStats?" in r.url for r in fetcher.calls)
    assert {
        r[0]
        for r in admin.execute(
            "SELECT kind FROM app.bing_import_generations WHERE site_id=%s", (scope.site_id,)
        )
    } == {"performance", "page_performance"}
    sources = strategy_call(api, **common(identity_context, scope), action="sources")
    assert len(sources["bing_pages"]["records"]) == 1
    assert sources["bing_pages"]["records"][0]["coverage"]["complete"] is False


@pytest.mark.parametrize(
    "method,url,allowed",
    [
        ("POST", "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live", True),
        ("POST", "https://api.dataforseo.com/v3/backlinks/summary/live", True),
        ("GET", "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live", False),
        ("POST", "https://api.dataforseo.com/v3/serp/google/organic/task_post", False),
        ("POST", "https://api.dataforseo.com.evil.invalid/v3/backlinks/summary/live", False),
        (
            "GET",
            "https://www.bing.com/webmaster/api.svc/json/GetPageStats?siteUrl=https%3A%2F%2Fexample.invalid%2F",
            True,
        ),
        ("GET", "https://www.bing.com/search?q=widgets", False),
        ("POST", "https://www.bing.com/webmaster/api.svc/json/GetPageStats", False),
    ],
)
def test_new_connector_routes_are_exact_allow_and_deny(admin, method, url, allowed):
    profile = "bing_api" if "bing.com" in url else "dataforseo"
    assert admin.execute(
        "SELECT control.owner_connector_request_allowed(%s,%s,%s)", (profile, method, url)
    ).fetchone() == (allowed,)


def test_bing_owner_projection_requires_current_verified_owner_and_fresh_mfa(
    pages_context, admin, identity, identity_context, scopes
):
    from signal_core.session_tokens import hash_session_token

    scope, site, binding, _, _ = pages_context
    args = (
        hash_session_token(identity_context["session_token"]),
        identity_context["generation"],
        scope.site_id,
    )
    read = "SELECT control.read_owner_bing_connector(%s,%s,%s)"
    assert identity.execute(read, args).fetchone()[0] == {"availability": "denied"}
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE id=%s",
        (identity_context["tenant_session_id"],),
    )
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (identity_context["identity_session_id"],),
    )
    assert identity.execute(read, args).fetchone()[0] == {
        "availability": "bound",
        "binding_id": str(binding),
        "site_url": site,
    }
    for changed in [
        (args[0], "wrong-generation", scope.site_id),
        (args[0], args[1], scopes[1].site_id),
        (args[0], args[1], scopes[2].site_id),
    ]:
        assert identity.execute(read, changed).fetchone()[0] == {"availability": "denied"}
    admin.execute(
        "UPDATE app.sessions SET auth_time=clock_timestamp()-interval '6 minutes' WHERE id=%s",
        (identity_context["tenant_session_id"],),
    )
    assert identity.execute(read, args).fetchone()[0] == {"availability": "denied"}


def test_concurrent_dual_cap_reservations_cannot_overspend(
    paid, api, admin, scopes, identity_context
):
    from dataclasses import asdict

    from signal_core.session_tokens import hash_session_token

    _, credential, fetcher, _ = paid
    scope = scopes[0]
    configure(api, identity_context, scope.site_id, "cap", cap=90000)

    def reserve(index):
        query = DataForSeoQuery("volume", "widgets " + str(index), 2840, "en")
        with workflow_connection() as connection:
            return connection.execute(
                "SELECT control.reserve_strategy_dataforseo(" + ",".join(["%s"] * 12) + ")",
                (
                    hash_session_token(identity_context["session_token"]),
                    identity_context["generation"],
                    scope.site_id,
                    scope.tenant_id,
                    scope.site_id,
                    uuid4(),
                    credential,
                    "volume",
                    query.endpoint,
                    hashlib.sha256(query.body()).digest(),
                    Jsonb(asdict(query)),
                    90000,
                ),
            ).fetchone()[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(reserve, range(2))) == ["cap_exhausted", "reserved"]
    assert admin.execute(
        "SELECT spend_cents,total_count FROM app.autonomy_weekly_usage WHERE site_id=%s",
        (scope.site_id,),
    ).fetchone() == (9, 1)
    assert not fetcher.calls


def test_provider_egress_additions_compose_with_other_owner_profiles(admin):
    """Later migrations extend the live owner egress contract instead of restating it."""
    for profile in (
        "github_rest",
        "gsc_api",
        "pagespeed",
        "webflow",
        "webflow_oauth",
        "webflow_revoke",
        "bing_oauth_token",
        "bing_api",
        "dataforseo",
    ):
        assert admin.execute(
            "SELECT control.sql_dispatch_value_allowed("
            "'owner_connector_egress_operations_profile_check',%s)",
            (profile,),
        ).fetchone() == (True,), profile
    # Robots reads for every read-only owner profile are checked as GET.
    for profile in ("github_rest", "gsc_api", "pagespeed", "bing_api", "webflow"):
        assert admin.execute(
            "SELECT control.owner_connector_robots_method(%s,'https://api.example.invalid/read')",
            (profile,),
        ).fetchone() == ("GET",)
