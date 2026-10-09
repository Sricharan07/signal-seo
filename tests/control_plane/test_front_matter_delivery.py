import hashlib
import json
from uuid import uuid4

import psycopg
import pytest
from signal_core.candidate_recipe_inbox import decide_authenticated_candidate_recipe_revision
from signal_core.proposals import ApprovalPermissionDenied

from tests.control_plane.test_astro_delivery import _seal, _setup
from tests.control_plane.test_github_unprotected_base import accept_sql, fresh_mfa


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def release_manager():
    import os

    with psycopg.connect(
        os.environ["SIGNAL_TEST_RELEASE_MANAGER_DSN"], autocommit=True
    ) as connection:
        yield connection


@pytest.mark.anyio
@pytest.mark.parametrize("framework", ["astro", "eleventy", "nextjs"])
@pytest.mark.parametrize("shared,unprotected", [(False, False), (True, False), (True, True)])
async def test_f1_exact_builds_mfa_owner_only_scope_tenant_and_role_negatives(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_ingest,
    release_manager,
    scopes,
    identity_context,
    tmp_path,
    framework,
    shared,
    unprotected,
):
    data = await _setup(
        admin,
        identity,
        api,
        scheduler,
        workflow,
        crawl_ingest,
        release_manager,
        scopes,
        identity_context,
        tmp_path,
        front_matter=True,
        framework=framework,
        shared=shared,
        version="1.0."
        + str(
            10
            + ["astro", "eleventy", "nextjs"].index(framework) * 3
            + int(shared)
            + int(unprotected)
        ),
    )
    scope, context, extension, records, manifest, impact, revision, key = data
    token = hashlib.sha256(context["session_token"].encode()).digest()
    if unprotected:
        from types import SimpleNamespace

        admin.execute(
            "UPDATE app.github_read_bindings SET protected=false WHERE id=%s",
            (extension.binding_id,),
        )
        assert _seal(identity, data)[3] == "build_unavailable"
        fresh_mfa(admin, context)
        assert (
            accept_sql(identity, scope, context, SimpleNamespace(id=extension.binding_id))
            == "active"
        )
    for mutation in (
        "count",
        "class",
        "lock",
        "format",
        "toolchain",
        "adapter",
        "authority",
        "claims",
        "layout",
        "config",
        "workflow",
        "sample",
        "page",
        "patch",
    ):
        m = json.loads(json.dumps(manifest))
        if mutation == "count":
            m["built_impact"]["page_count"] += 1
        elif mutation == "class":
            m["approval_class"] = "A4" if not shared else "A2"
        elif mutation == "lock":
            m["built_impact"]["lockfile_sha256"] = "0" * 64
        elif mutation == "format":
            m["framework"] = "hugo"
        elif mutation == "toolchain":
            m["build_receipt"]["toolchain"] = "synthetic-unpinned"
        elif mutation == "adapter":
            m["content_adapter"] = "instructions"
        elif mutation == "authority":
            m["autonomy_eligible"] = True
        elif mutation == "claims":
            m["claim_review_required"] = False
        elif mutation == "layout":
            m["source_path"] = "layouts/page.md"
        elif mutation == "config":
            m["source_path"] = "src/content.config.md"
        elif mutation == "workflow":
            m["source_path"] = ".github/workflows/page.md"
        elif mutation == "sample":
            m["built_impact"]["samples"][0]["after"] = "forged"
        elif mutation == "page":
            m["evidence"]["page_url"] = "https://other.example.invalid/"
        elif mutation == "patch":
            m["patch_sha256"] = "0" * 64
        assert _seal(identity, data, manifest=m)[3] != "sealed", mutation
    other_site = (scopes[1], context, *data[2:])
    assert _seal(identity, other_site)[3] != "sealed"
    admin.execute(
        "UPDATE app.memberships SET role_key='editor' WHERE id=%s", (context["membership_id"],)
    )
    assert _seal(identity, data)[3] == "permission_denied"
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE id=%s", (context["membership_id"],)
    )
    row = _seal(identity, data)
    assert row[3] == "sealed" and _seal(identity, data)[2] is True
    assert impact.approval_class == ("A4" if shared else "A2")
    args = (token, scope.site_id, context["generation"], revision)
    fresh_mfa(admin, context)
    assert identity.execute(
        "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)", args
    ).fetchone() == ("decision_stale",)
    decision = dict(
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
        revision_id=revision,
        expected_revision_sha256=bytes(row[1]).hex(),
        decision_id=uuid4(),
        decision="approved",
    )
    fresh_mfa(admin, context, age="6 minutes")
    with pytest.raises(ApprovalPermissionDenied):
        decide_authenticated_candidate_recipe_revision(identity, **decision)
    fresh_mfa(admin, context)
    approved = decide_authenticated_candidate_recipe_revision(identity, **decision)
    assert approved.decision_channel == "dashboard"
    assert identity.execute(
        "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)", args
    ).fetchone() == ("eligible",)
    assert admin.execute(
        "SELECT control.weekly_revision_a2_policy(%s,%s,%s)",
        (scope.tenant_id, scope.site_id, revision),
    ).fetchone() == (False,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute(
            "SELECT control.seal_front_matter_recipe_revision(" + ",".join(["NULL"] * 17) + ")"
        )
    fresh_mfa(admin, context, age="6 minutes")
    assert identity.execute(
        "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)", args
    ).fetchone() == ("step_up_required",)
    if unprotected:
        fresh_mfa(admin, context)
        admin.execute(
            "UPDATE app.github_read_bindings SET risk_generation=risk_generation+1 WHERE id=%s",
            (extension.binding_id,),
        )
        assert (
            identity.execute(
                "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)", args
            ).fetchone()[0]
            != "eligible"
        )
