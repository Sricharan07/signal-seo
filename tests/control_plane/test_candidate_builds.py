from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from signal_core.authorization import AuthorizationDenied
from signal_core.candidate_build import EMPTY_PATCH_SHA256, NODE_IMAGE, CandidateBuildPlan
from signal_core.candidate_build_service import (
    CandidateBuildConflict,
    CandidateBuildUnavailable,
    dispatch_candidate_build,
    finish_candidate_build,
    prepare_candidate_build,
    read_candidate_build,
)
from signal_core.candidate_sandbox import CandidateSandboxOutcome
from signal_core.github_pr_extension import (
    observe_github_pr_extension,
    revoke_github_pr_extension,
)

from tests.control_plane.test_github_pr_extension import _active_binding, _credential, _provider


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _active_extension(admin, identity, scopes, identity_context):
    scope, context, binding = _active_binding(admin, identity, scopes, identity_context)
    credential, bao = _credential()
    transport, _ = _provider()
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
    return scope, context, extension


def _plan(extension):
    return CandidateBuildPlan(
        extension.base_sha,
        extension.tree_sha,
        EMPTY_PATCH_SHA256,
        NODE_IMAGE,
        ("npm", "run", "build"),
        ".next",
        (("app/page.tsx", b"base"),),
    )


def _prepare(identity, scope, context, extension, *, key=None):
    return prepare_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        extension_id=extension.id,
        idempotency_key=key or uuid4(),
        plan=_plan(extension),
    )


def _result():
    return CandidateSandboxOutcome(
        "passed",
        0,
        "c" * 64,
        12,
        ((".next/index.html", "d" * 64, 2),),
    )


@pytest.mark.anyio
async def test_build_receipt_is_exact_immutable_and_idempotent(
    admin, identity, api, scopes, identity_context
):
    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    key = uuid4()
    prepared = _prepare(identity, scope, context, extension, key=key)
    assert prepared.status == "prepared"
    dispatch_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
    )
    finished = finish_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
        result=_result(),
    )
    assert (finished.status, finished.base_sha, finished.patch_sha256) == (
        "completed",
        "a" * 40,
        EMPTY_PATCH_SHA256,
    )
    assert (finished.command, finished.exit_class, finished.logs_sha256) == (
        "npm run build",
        "passed",
        "c" * 64,
    )
    assert finished.artifacts == ((".next/index.html", "d" * 64, 2),)
    replayed = _prepare(identity, scope, context, extension, key=key)
    assert replayed.replayed and replayed.status == "completed" and replayed.id == prepared.id
    assert (
        finish_candidate_build(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=prepared,
            result=_result(),
        ).id
        == prepared.id
    )
    assert admin.execute(
        "SELECT count(*) FROM app.candidate_build_receipts WHERE build_id = %s",
        (prepared.id,),
    ).fetchone() == (1,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT count(*) FROM app.candidate_build_receipts")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.candidate_build_receipts SET log_bytes = 0 WHERE build_id = %s",
            (prepared.id,),
        )


@pytest.mark.anyio
async def test_lost_receipt_never_restarts_dispatched_build(
    admin, identity, scopes, identity_context
):
    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    key = uuid4()
    prepared = _prepare(identity, scope, context, extension, key=key)
    dispatch_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
    )
    assert (
        read_candidate_build(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            build_id=prepared.id,
        ).status
        == "dispatched"
    )
    replayed = _prepare(identity, scope, context, extension, key=key)
    assert replayed.replayed and replayed.status == "dispatched"
    with pytest.raises(CandidateBuildUnavailable, match="CANDIDATE_DISPATCH_UNKNOWN"):
        dispatch_candidate_build(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=replayed,
        )
    assert admin.execute(
        "SELECT count(*) FROM app.candidate_build_receipts WHERE build_id = %s",
        (prepared.id,),
    ).fetchone() == (0,)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "exit_class,exit_code",
    [("timeout", None), ("oom", 137), ("crash", 42), ("output_limit", None)],
)
async def test_failed_builds_store_only_bounded_failure_receipts(
    admin, identity, scopes, identity_context, exit_class, exit_code
):
    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    prepared = _prepare(identity, scope, context, extension)
    dispatch_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
    )
    finished = finish_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
        result=CandidateSandboxOutcome(exit_class, exit_code, "e" * 64, 65536, ()),
    )
    assert finished.status == "completed"
    assert finished.exit_class == exit_class
    assert finished.exit_code == exit_code
    assert finished.log_bytes == 65536
    assert finished.artifacts == ()


@pytest.mark.anyio
async def test_wrong_scope_revocation_and_role_reduction_block_dispatch(
    admin, identity, scopes, identity_context
):
    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    key = uuid4()
    prepared = _prepare(identity, scope, context, extension, key=key)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        prepare_candidate_build(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            extension_id=extension.id,
            idempotency_key=uuid4(),
            plan=replace(_plan(extension), toolchain="node:untrusted"),
        )
    with pytest.raises(CandidateBuildConflict):
        prepare_candidate_build(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            extension_id=uuid4(),
            idempotency_key=key,
            plan=_plan(extension),
        )
    revoke_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        extension_id=extension.id,
    )
    with pytest.raises(CandidateBuildUnavailable):
        dispatch_candidate_build(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=prepared,
        )
    assert admin.execute(
        "SELECT status FROM app.candidate_build_intents WHERE id = %s",
        (prepared.id,),
    ).fetchone() == ("prepared",)
    admin.execute(
        "UPDATE app.memberships SET role_key = 'analyst', authorization_epoch = 3 WHERE id = %s",
        (context["membership_id"],),
    )
    with pytest.raises(AuthorizationDenied):
        dispatch_candidate_build(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=prepared,
        )


@pytest.mark.anyio
async def test_invalid_receipt_rolls_back_without_claiming_success(
    admin, identity, scopes, identity_context
):
    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    prepared = _prepare(identity, scope, context, extension)
    dispatch_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
    )
    with pytest.raises(CandidateBuildConflict):
        finish_candidate_build(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=prepared,
            result=CandidateSandboxOutcome(
                "passed",
                0,
                "c" * 64,
                12,
                (("../escape", "d" * 64, 2),),
            ),
        )
    assert admin.execute(
        "SELECT status FROM app.candidate_build_intents WHERE id = %s",
        (prepared.id,),
    ).fetchone() == ("dispatched",)
    assert admin.execute(
        "SELECT count(*) FROM app.candidate_build_receipts WHERE build_id = %s",
        (prepared.id,),
    ).fetchone() == (0,)
