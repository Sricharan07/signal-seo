"""Committed crawl, shared egress, exact builds and owner/workload safety on PostgreSQL."""

import hashlib
import json
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
import rfc8785
from signal_core.authorization import AuthorizationDenied
from signal_core.candidate_sandbox import CandidateSandboxOutcome
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_frontier import CrawlRunLimits
from signal_core.github_pr_extension import observe_github_pr_extension
from signal_core.github_pr_patch import git_tree_sha
from signal_core.github_read_binding import GitHubSharedEgressTransport, finish_github_read_binding
from signal_core.internal_link_service import (
    load_internal_link_sources,
    seal_internal_link_revision,
)
from signal_core.internal_link_skill import InternalLinkSkillPort, internal_link_skill_io
from signal_core.internal_linking import RECIPE, link_opportunities, salient_terms
from signal_core.recipe_releases import RecipeReleaseUnavailable
from signal_core.seo_strategy_service import refresh_strategy, strategy_call
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import SharedEgressProvider
from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable
from signal_core.weekly_control import set_site_paused
from signal_core.weekly_skill_ports import WeeklySkillConnection, local_skill_ports
from signal_core.weekly_skills import SkillPermit, WeeklySkills

from tests.control_plane.test_full_site_crawl import _command, _executor
from tests.control_plane.test_github_pr_extension import _credential
from tests.control_plane.test_github_read_binding import _owner_site, _prepare, _snapshot, _target
from tests.control_plane.test_github_unprotected_base import fresh_mfa
from tests.control_plane.test_shared_egress import authority
from tests.control_plane.test_technical_recipes import TimedFetcher, _register_release
from tests.control_plane.test_technical_recipes import release_manager as release_manager
from tests.control_plane.test_weekly_skills import Recovery, admit, open_skills, workflow_connection
from tests.control_plane.test_weekly_skills import skill_setup as skill_setup
from tests.delivery.autonomy_delivery_support import DeliveryRepositoryDouble


@pytest.fixture
def anyio_backend():
    return "asyncio"


def html(extra="", paragraph="Garden irrigation systems reduce water waste in dry weather."):
    return (
        "<html><head><title>Garden irrigation systems</title></head><body>"
        f"<h1>Garden irrigation</h1>{extra}<main><p>{paragraph}</p></main></body></html>"
    ).encode()


class BuildDouble:
    def __init__(self):
        self.failure = None
        self.calls = 0

    def run(self, plan):
        self.calls += 1
        artifacts = tuple(
            ("_site/" + p, hashlib.sha256(b).hexdigest(), len(b))
            for p, b in plan.files
            if p.endswith(".html")
        )
        if self.failure == "extra":
            artifacts += (("_site/extra.txt", "a" * 64, 1),)
        return CandidateSandboxOutcome(
            "crash" if self.failure == "crash" else "passed",
            1 if self.failure == "crash" else 0,
            "e" * 64,
            1,
            () if self.failure == "crash" else artifacts,
        )


@pytest.fixture
async def links(
    admin,
    api,
    identity,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    release_manager,
    tmp_path,
):
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    origin = admin.execute(
        "SELECT primary_origin FROM app.sites WHERE id=%s", (scope.site_id,)
    ).fetchone()[0]
    body = html('<a href="/second">Supporting article</a>')
    second = html('<a href="/water.html">Water guide</a>')

    class Fetch(TimedFetcher):
        def request(self, outbound, *, policy):
            if outbound.url.endswith("/water.html"):
                self.page_body = html()
            return super().request(outbound, policy=policy)

    command, run = _command(api, scheduler, workflow, scope, "synthetic-internal-link-crawl")
    executor = _executor(tmp_path, Fetch(origin, page_body=body, second_body=second))
    executor._limits = CrawlRunLimits(max_urls=4, max_depth=2, max_duration_seconds=30)
    executor.run(command, first_run_id=run)
    target = _target(content_path="index.html")
    binding = _prepare(identity, scope, context, target=target)
    finish_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=binding,
        snapshot=_snapshot(target),
    )
    store = EncryptedLocalArtifactStore(tmp_path / "github")
    double = DeliveryRepositoryDouble(origin)
    double.files.update(
        {
            "index.html": body,
            "water.html": html(
                paragraph="Water irrigation techniques keep garden plants healthy during summer."
            ),
        }
    )
    double.base_tree = git_tree_sha(double.files)
    double.trees = {double.base_tree: double.files.copy()}
    double.commits = {double.base_sha: {"sha": double.base_sha, "tree": {"sha": double.base_tree}}}
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "synthetic-internal-link-github-" + uuid4().hex,
        store,
        origin_override="https://api.github.com",
        github_profile=True,
    )
    provider = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        double,
        "worker.internal-links",
        OriginAdmissionPolicy(),
        None,
        "connector",
    )
    transport = GitHubSharedEgressTransport(provider)
    credential, bao = _credential()
    extension = await observe_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=uuid4(),
        credential=credential,
        github_transport=transport,
        openbao_transport=bao,
    )
    version = admin.execute(
        "SELECT coalesce(max(version_patch),0)+1 FROM control.recipe_releases WHERE recipe_key=%s",
        (RECIPE,),
    ).fetchone()[0]
    release, _ = _register_release(
        admin, release_manager, RECIPE, reviewed=True, version=f"1.0.{version}"
    )
    common = dict(
        session_token=context["session_token"],
        generation=context["generation"],
        site_id=scope.site_id,
    )
    sources = load_internal_link_sources(identity, **common)
    assert len(sources["pages"]) == 3, sources
    opportunity = next(
        p for p in link_opportunities(sources["pages"], origin) if p["source_url"] == origin + "/"
    )
    runner = BuildDouble()
    args = dict(
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        extension_id=extension.id,
        source_id=UUID(opportunity["source_id"]),
        target_id=UUID(opportunity["target_id"]),
        recipe_release_id=release,
        idempotency_key=uuid4(),
        build_idempotency_key=uuid4(),
        credential=credential,
        github_transport=transport,
        openbao_transport=bao,
        runner=runner,
    )
    return SimpleNamespace(
        scope=scope,
        context=context,
        sources=sources,
        args=args,
        runner=runner,
        double=double,
        provider=provider,
        common=common,
    )


@pytest.mark.anyio
async def test_committed_graph_exact_candidate_replay_strategy_and_existing_approval(
    admin,
    api,
    identity,
    links,
):
    for page in links.sources["pages"]:
        assert tuple(
            admin.execute(
                "SELECT control.internal_link_terms(%s)", (psycopg.types.json.Jsonb(page),)
            ).fetchone()[0]
        ) == salient_terms(page)
    refresh_strategy(api, **links.common)
    items = strategy_call(api, **links.common, action="read")["snapshot"]["payload"]["strategy"][
        "items"
    ]
    assert any(
        i["kind"] == "internal_link"
        and i["priority"]["inputs"]["incoming_count"] == 1
        and i["action"] is None
        for i in items
    )
    sealed = await seal_internal_link_revision(identity, api, **links.args)
    assert not sealed.replayed and links.runner.calls == 2
    before = len(links.double.calls)
    assert (await seal_internal_link_revision(identity, api, **links.args)).replayed
    assert len(links.double.calls) == before and links.runner.calls == 2
    row = identity.execute(
        "SELECT * FROM control.read_authenticated_candidate_recipe_inbox(%s,%s,%s)",
        (
            hash_session_token(links.context["session_token"]),
            links.scope.site_id,
            links.context["generation"],
        ),
    ).fetchone()
    assert row[0] == sealed.id and row[8] == "pending"
    assert not admin.execute(
        "SELECT control.recipe_autonomy_eligible(%s)", (links.args["recipe_release_id"],)
    ).fetchone()[0]
    denied = identity.execute(
        "SELECT * FROM control.decide_authenticated_candidate_recipe_revision("
        "%s,%s,%s,%s,%s,%s,%s)",
        (
            hash_session_token(links.context["session_token"]),
            links.scope.site_id,
            links.context["generation"],
            sealed.id,
            bytes.fromhex(sealed.revision_sha256),
            uuid4(),
            "approved",
        ),
    ).fetchone()
    assert denied[15] == "approval_permission_denied"
    fresh_mfa(admin, links.context)
    decision = identity.execute(
        "SELECT * FROM control.decide_authenticated_candidate_recipe_revision("
        "%s,%s,%s,%s,%s,%s,%s)",
        (
            hash_session_token(links.context["session_token"]),
            links.scope.site_id,
            links.context["generation"],
            sealed.id,
            bytes.fromhex(sealed.revision_sha256),
            uuid4(),
            "approved",
        ),
    ).fetchone()
    assert decision[8] == "approved" and decision[15] == "decided"
    operation = identity.execute(
        "SELECT * FROM control.prepare_github_pr_operation(%s,%s,%s,%s,%s,%s,%s)",
        (
            hash_session_token(links.context["session_token"]),
            links.scope.site_id,
            links.context["generation"],
            sealed.id,
            bytes.fromhex(sealed.revision_sha256),
            uuid4(),
            hashlib.sha256(b"synthetic internal link intent").digest(),
        ),
    ).fetchone()
    assert operation[5] == "prepared"
    refresh_strategy(api, **links.common)
    items = strategy_call(api, **links.common, action="read")["snapshot"]["payload"]["strategy"][
        "items"
    ]
    assert any(i["action"] == {"kind": "inbox", "revision_id": str(sealed.id)} for i in items)
    assert not links.double.pulls
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT * FROM app.internal_link_candidates")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute("DELETE FROM app.internal_link_candidates WHERE revision_id=%s", (sealed.id,))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "negative", ["site", "tenant", "role", "generation", "target", "unreviewed", "crash", "extra"]
)
async def test_fail_closed_before_sealing(
    admin, api, identity, links, scopes, release_manager, negative
):
    args = dict(links.args)
    expected = TechnicalRecipeUnavailable
    if negative in {"site", "tenant"}:
        args["site_id"] = scopes[1 if negative == "site" else 2].site_id
        expected = AuthorizationDenied
    elif negative == "role":
        admin.execute(
            "UPDATE app.memberships SET role_key='analyst',"
            "authorization_epoch=authorization_epoch+1 WHERE id=%s",
            (links.context["membership_id"],),
        )
        expected = AuthorizationDenied
    elif negative == "generation":
        args["current_recovery_generation"] = "wrong-generation"
        expected = AuthorizationDenied
    elif negative == "target":
        args["target_id"] = uuid4()
    elif negative == "unreviewed":
        version = admin.execute(
            "SELECT max(version_patch)+1 FROM control.recipe_releases WHERE recipe_key=%s",
            (RECIPE,),
        ).fetchone()[0]
        args["recipe_release_id"], _ = _register_release(
            admin, release_manager, RECIPE, reviewed=False, version=f"1.0.{version}"
        )
        expected = RecipeReleaseUnavailable
    else:
        links.runner.failure = negative
        if negative == "extra":
            # Only candidate artifacts diverge; the baseline remains unchanged.
            original = links.runner.run

            def run(plan):
                links.runner.failure = None if links.runner.calls == 0 else "extra"
                return original(plan)

            links.runner.run = run
    with pytest.raises(expected):
        await seal_internal_link_revision(identity, api, **args)
    assert not admin.execute(
        "SELECT 1 FROM app.internal_link_candidates WHERE site_id=%s", (links.scope.site_id,)
    ).fetchall()
    assert not links.double.pulls


@pytest.mark.anyio
async def test_weekly_registry_candidate_only_exact_scope_pause_and_replay(
    admin,
    api,
    identity,
    workflow,
    links,
    skill_setup,
):
    _owner_site(admin, identity, [links.scope], links.context)
    cycle = skill_setup()
    open_skills(workflow, cycle)
    engine = WeeklySkills(
        workflow_connection, Recovery(), ports=local_skill_ports(workflow_connection)
    )
    # Run only through strategy so candidate admission below is explicit.
    engine.ports.pop("internal_link_proposals", None)
    # Admission without a configured port is truthful and does not spend a draft unit.
    result, _ = admit(workflow, cycle, "internal_link_proposals", configured=False)
    assert result["reason"] == "PORT_UNCONFIGURED"
    from signal_core.weekly_skill_ports import StrategySkillPort

    result, handle = admit(workflow, cycle, "strategy_rebuild")
    permit = SkillPermit(
        UUID(cycle.site.tenant_id),
        UUID(cycle.site.site_id),
        cycle.cycle_id,
        "strategy_rebuild",
        "test-generation-1",
        handle,
        tuple(result["plan"]),
        cycle.week_start,
    )
    outcome = await StrategySkillPort(workflow_connection).run(permit, lambda: None)
    assert outcome.outcome == "completed"
    workflow.execute(
        "SELECT control.record_weekly_skill(%s,%s,%s,%s,%s,%s,%s)",
        (
            permit.tenant_id,
            permit.site_id,
            permit.cycle_id,
            permit.stage,
            outcome.outcome,
            outcome.detail_code,
            psycopg.types.json.Jsonb(list(outcome.evidence_refs)),
        ),
    )
    result, handle = admit(workflow, cycle, "internal_link_proposals")
    assert result["state"] == "started" and result["units"] >= 1
    permit = replace(
        permit, stage="internal_link_proposals", handle=handle, plan=tuple(result["plan"])
    )

    def releases():
        return psycopg.connect(os.environ["SIGNAL_TEST_API_DSN"], autocommit=True)

    def io(permit, guard):
        return internal_link_skill_io(
            permit,
            guard,
            credential=links.args["credential"],
            provider=links.provider,
            runner=links.runner,
            openbao_transport=links.args["openbao_transport"],
        )

    port = InternalLinkSkillPort(workflow_connection, releases, io)
    outcome = await port.run(permit, lambda: None)
    assert outcome.outcome == "completed" and outcome.evidence_refs
    assert (
        not links.double.pulls
        and not admin.execute(
            "SELECT 1 FROM app.candidate_recipe_review_decisions WHERE site_id=%s",
            (links.scope.site_id,),
        ).fetchall()
    )
    with pytest.raises(PermissionError):
        WeeklySkillConnection(workflow, permit).execute(
            "SELECT control.decide_authenticated_candidate_recipe_revision(%s)",
            (permit.handle_hash,),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        workflow.execute(
            "SELECT control.internal_link_skill_read_github_pr_extension(%s,%s,%s,%s)",
            (permit.handle_hash, permit.site_id, permit.generation, uuid4()),
        )
    for site, generation in [(uuid4(), permit.generation), (permit.site_id, "wrong-generation")]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            workflow.execute(
                "SELECT control.internal_link_skill_internal_link_sources(%s,%s,%s)",
                (permit.handle_hash, generation, site),
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute(
            "SELECT control.internal_link_skill_internal_link_sources(%s,%s,%s)",
            (permit.handle_hash, permit.generation, permit.site_id),
        )
    before = len(links.double.calls)
    assert (await port.run(permit, lambda: None)).evidence_refs == outcome.evidence_refs
    assert len(links.double.calls) == before
    set_site_paused(
        api,
        session_token=links.context["session_token"],
        site_id=links.scope.site_id,
        recovery_generation=links.context["generation"],
        paused=True,
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        workflow.execute(
            "SELECT control.internal_link_skill_internal_link_sources(%s,%s,%s)",
            (permit.handle_hash, permit.generation, permit.site_id),
        )
    engine.ports["internal_link_proposals"] = port
    await engine.run(cycle, "research")
    assert len(links.double.calls) == before


@pytest.mark.anyio
async def test_sql_sealer_refuses_forged_scope_autonomy_cap_duplicate_and_receipt(
    admin,
    api,
    identity,
    links,
):
    sealed = await seal_internal_link_revision(identity, api, **links.args)
    canonical = admin.execute(
        "SELECT canonical_manifest FROM app.candidate_recipe_revisions WHERE id=%s", (sealed.id,)
    ).fetchone()[0]
    packet = json.loads(canonical)

    def seal(value):
        return identity.execute(
            "SELECT control.seal_internal_link_revision(%s,%s,%s,%s,%s,%s)",
            (
                hash_session_token(links.context["session_token"]),
                links.context["generation"],
                links.scope.site_id,
                uuid4(),
                uuid4(),
                rfc8785.dumps(value),
            ),
        ).fetchone()[0]

    assert seal(packet) == "duplicate_link_or_anchor"
    for field, value in [
        ("target_url", "https://other.example.invalid/water.html"),
        ("page_cap", 4),
    ]:
        changed = json.loads(canonical)
        changed["internal_link"][field] = value
        assert seal(changed) in {"evidence_unavailable", "invalid_revision"}
    changed = json.loads(canonical)
    changed["internal_link"]["page_cap"] = 1
    assert seal(changed) == "page_cap"
