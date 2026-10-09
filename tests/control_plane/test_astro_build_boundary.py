import hashlib
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.authorization import AuthorizationDenied
from signal_core.candidate_build_service import (
    CandidateBuildConflict,
    CandidateBuildUnavailable,
    build_candidate_from_github,
    dispatch_candidate_build,
    finish_candidate_build,
    prepare_candidate_build,
    read_candidate_build,
)
from signal_core.candidate_sandbox import CandidateSandboxOutcome
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_http import EgressHttpResult
from signal_core.github_pr_extension import (
    observe_github_pr_extension,
)
from signal_core.github_read_binding import finish_github_read_binding
from signal_core.npm_registry import NpmRegistryCache, NpmRegistryUnavailable, locked_dependencies
from signal_core.shared_egress import SharedEgressProvider

from tests.control_plane.test_candidate_builds import _active_extension, _plan
from tests.control_plane.test_github_pr_extension import _credential, _provider
from tests.control_plane.test_github_read_binding import _owner_site, _prepare, _snapshot, _target
from tests.control_plane.test_shared_egress import Fetcher, authority
from tests.control_plane.test_shared_egress import request as synthetic_request
from tests.control_plane.test_shared_egress import response as synthetic_response
from tests.tooling.astro_build_support import package_tarball, source_files
from tests.tooling.test_candidate_build import _sha

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def cache():
    with tempfile.TemporaryDirectory(prefix="0126-cache-", dir=ROOT / ".runtime") as directory:
        yield NpmRegistryCache(worktree_root=Path(directory))


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["success", "corrupt", "redirect", "error"])
async def test_registry_cache_uses_real_shared_egress_and_durable_profile(
    mode, admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, cache
):
    tarball = package_tarball()
    files = source_files(tarball=tarball)
    manifest = locked_dependencies(files["package.json"], files["package-lock.json"])
    url = manifest.tarballs[0].url
    store = EncryptedLocalArtifactStore(cache.directory.parent / "artifacts")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "synthetic-npm-" + uuid4().hex,
        store,
        origin_override="https://registry.npmjs.org",
        github_profile=True,
    )
    body = tarball if mode != "corrupt" else b"corrupt"
    status = {"success": 200, "corrupt": 200, "redirect": 302, "error": 503}[mode]
    fetcher = Fetcher(
        EgressHttpResult(
            schema_version=1,
            request_url=url,
            final_url=url,
            method="GET",
            outcome="fetched",
            http_status=status,
            media_type="application/octet-stream",
            response_headers=(("content-type", "application/octet-stream"),),
            resolved_address=synthetic_response(
                synthetic_request(policy.allowed_origins[0])
            ).resolved_address,
            body=body,
            body_sha256=hashlib.sha256(body).hexdigest(),
            decoded_bytes=len(body),
            elapsed_ms=5,
        )
    )
    provider = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        fetcher,
        "worker.synthetic-npm",
        OriginAdmissionPolicy(),
        None,
        purpose="connector",
    )
    if mode == "success":
        first = await cache.fetch(manifest, provider)
        assert first[0][1] == tarball
        assert await cache.fetch(manifest, provider) == first
        assert len(fetcher.calls) == 1
        (cache.directory / first[0][0]).write_bytes(b"corrupt-cache")
        with pytest.raises(NpmRegistryUnavailable, match="INTEGRITY"):
            await cache.fetch(manifest, provider)
        assert len(fetcher.calls) == 1
    else:
        with pytest.raises(NpmRegistryUnavailable):
            await cache.fetch(manifest, provider)
        assert not list(cache.directory.glob("*.tgz"))
        assert len(fetcher.calls) == 1
    rows = admin.execute(
        "SELECT egress_profile,method,credentialed,request_bytes,request_url,state "
        "FROM app.egress_operations WHERE tenant_id=%s AND site_id=%s",
        (run.tenant_id, run.site_id),
    ).fetchall()
    assert rows == [("npm_registry", "GET", False, 0, url, "observed")]
    for profile in (
        replace(provider, purpose="model"),
        replace(provider, policy=replace(policy, max_redirects=1)),
    ):
        with pytest.raises(NpmRegistryUnavailable, match="EGRESS_UNAVAILABLE"):
            await cache.fetch(manifest, profile)


async def _astro_build(admin, identity, scopes, context, *, bind=True):
    scope, context, extension = await _active_extension(admin, identity, scopes, context)
    admin.execute(
        "UPDATE app.github_pr_extensions SET framework='astro' WHERE id=%s", (extension.id,)
    )
    manifest = locked_dependencies(
        source_files()["package.json"], source_files()["package-lock.json"]
    )
    plan = replace(
        _plan(extension),
        artifact_root="public-build",
        dependency_manifest=manifest if bind else None,
    )
    prepared = prepare_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        extension_id=extension.id,
        idempotency_key=uuid4(),
        plan=plan,
    )
    return scope, context, prepared, manifest


def _result(manifest):
    return CandidateSandboxOutcome(
        "passed",
        0,
        "c" * 64,
        12,
        (("public-build/index.html", "d" * 64, 12),),
        manifest.lockfile_sha256,
        (("public-build/index.html", "d" * 64, "Synthetic title", "Synthetic description"),),
    )


@pytest.mark.anyio
async def test_dependency_receipt_is_atomic_immutable_replay_safe_and_function_only(
    admin, identity, api, scopes, identity_context
):
    scope, context, prepared, manifest = await _astro_build(
        admin, identity, scopes, identity_context
    )
    args = dict(
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
    )
    dispatch_candidate_build(identity, **args)
    result = _result(manifest)
    finished = finish_candidate_build(identity, **args, result=result)
    assert finished.lockfile_sha256 == manifest.lockfile_sha256
    assert finished.built_pages == result.built_pages
    assert finish_candidate_build(identity, **args, result=result) == finished
    assert (
        read_candidate_build(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            build_id=prepared.id,
            include_dependencies=True,
        )
        == finished
    )
    with pytest.raises(CandidateBuildConflict):
        finish_candidate_build(
            identity,
            **args,
            result=replace(
                result, built_pages=(("public-build/index.html", "d" * 64, "Changed", None),)
            ),
        )
    for table in ("candidate_dependency_inputs", "candidate_dependency_receipts"):
        assert admin.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
            ("app." + table,),
        ).fetchone() == (True, True)
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(
                f"UPDATE app.{table} SET lockfile_sha256=%s WHERE build_id=%s",
                ("f" * 64, prepared.id),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(f"SELECT * FROM app.{table}")
        assert not admin.execute(
            "SELECT has_table_privilege('signal_identity',%s,'SELECT')", ("app." + table,)
        ).fetchone()[0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute(
            "SELECT control.finish_before_dependencies(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                hashlib.sha256(context["session_token"].encode()).digest(),
                scope.site_id,
                context["generation"],
                prepared.id,
                "passed",
                0,
                "c" * 64,
                12,
                Jsonb([]),
            ),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change", ["lock", "page_digest", "missing_html", "duplicate_page", "no_lock"]
)
async def test_dependency_receipt_cannot_omit_or_forge_built_html_or_lock(
    change, admin, identity, scopes, identity_context
):
    scope, context, prepared, manifest = await _astro_build(
        admin, identity, scopes, identity_context
    )
    args = dict(
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
    )
    dispatch_candidate_build(identity, **args)
    result = _result(manifest)
    if change == "lock":
        result = replace(result, lockfile_sha256="e" * 64)
    elif change == "page_digest":
        result = replace(result, built_pages=(("public-build/index.html", "e" * 64, None, None),))
    elif change == "missing_html":
        result = replace(
            result, artifacts=result.artifacts + (("public-build/other.html", "e" * 64, 12),)
        )
    elif change == "duplicate_page":
        result = replace(result, built_pages=result.built_pages * 2)
    else:
        result = replace(result, lockfile_sha256=None)
    with pytest.raises((CandidateBuildUnavailable, CandidateBuildConflict, AuthorizationDenied)):
        finish_candidate_build(identity, **args, result=result)
    assert (
        admin.execute(
            "SELECT count(*) FROM app.candidate_build_receipts WHERE build_id=%s", (prepared.id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.candidate_dependency_receipts WHERE build_id=%s",
            (prepared.id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        admin.execute(
            "SELECT status FROM app.candidate_build_intents WHERE id=%s", (prepared.id,)
        ).fetchone()[0]
        == "dispatched"
    )


@pytest.mark.anyio
async def test_unbound_astro_build_cannot_dispatch_and_authority_change_blocks_finish(
    admin, identity, scopes, identity_context
):
    scope, context, prepared, _ = await _astro_build(
        admin, identity, scopes, identity_context, bind=False
    )
    args = dict(
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
    )
    with pytest.raises(CandidateBuildUnavailable):
        dispatch_candidate_build(identity, **args)
    assert (
        admin.execute(
            "SELECT status FROM app.candidate_build_intents WHERE id=%s", (prepared.id,)
        ).fetchone()[0]
        == "prepared"
    )


@pytest.mark.anyio
async def test_dependency_receipts_respect_current_owner_scope_and_epochs(
    admin, identity, scopes, identity_context
):
    scope, context, prepared, manifest = await _astro_build(
        admin, identity, scopes, identity_context
    )
    token = hashlib.sha256(context["session_token"].encode()).digest()
    assert (
        identity.execute(
            "SELECT control.bind_candidate_dependencies(%s,%s,%s,%s,%s)",
            (
                token,
                scopes[1].site_id,
                context["generation"],
                prepared.id,
                manifest.lockfile_sha256,
            ),
        ).fetchone()[0]
        == "permission_denied"
    )
    args = dict(
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
    )
    dispatch_candidate_build(identity, **args)
    admin.execute(
        "UPDATE app.memberships SET role_key='analyst',authorization_epoch=2 WHERE id=%s",
        (context["membership_id"],),
    )
    with pytest.raises(AuthorizationDenied):
        finish_candidate_build(identity, **args, result=_result(manifest))
    assert (
        admin.execute(
            "SELECT count(*) FROM app.candidate_dependency_receipts WHERE build_id=%s",
            (prepared.id,),
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "url,method,credentialed",
    [
        ("https://registry.npmjs.org/astro", "GET", False),
        ("https://registry.npmjs.org/astro/-/astro-5.0.0.tgz?token=synthetic", "GET", False),
        ("https://registry.npmjs.org/astro/-/astro-5.0.0.tgz", "POST", True),
        ("https://registry.npmjs.org/astro/-/astro-5.0.0.tgz", "GET", True),
    ],
)
def test_registry_database_profile_cannot_be_borrowed_for_unclosed_requests(
    url,
    method,
    credentialed,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    cache,
):
    store = EncryptedLocalArtifactStore(cache.directory.parent / "artifacts")
    run, _, snapshot = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "synthetic-npm-" + uuid4().hex,
        store,
        origin_override="https://registry.npmjs.org",
        github_profile=True,
    )
    operation_id, request_hash = uuid4(), hashlib.sha256(b"synthetic-request").digest()

    class RollbackAdmission(Exception):
        pass

    with pytest.raises(RollbackAdmission), crawl_admission.transaction():
        deadline = time.monotonic() + 45
        while True:
            row = crawl_admission.execute(
                "SELECT outcome FROM control.begin_shared_egress_operation("
                + ",".join(["%s"] * 19)
                + ")",
                (
                    run.tenant_id,
                    run.site_id,
                    run.run_id,
                    "worker.synthetic-npm",
                    operation_id,
                    "connector",
                    method,
                    url,
                    "https://registry.npmjs.org",
                    request_hash,
                    request_hash if method == "POST" else None,
                    1 if method == "POST" else 0,
                    5242880,
                    credentialed,
                    snapshot.snapshot_id,
                    "robots_not_found",
                    1,
                    1000,
                    30,
                ),
            ).fetchone()
            if row[0] == "admitted":
                break
            assert row[0] in {"deferred_backoff", "deferred_politeness"}
            assert time.monotonic() < deadline
            time.sleep(1)
        with pytest.raises(psycopg.errors.InvalidParameterValue), crawl_admission.transaction():
            crawl_admission.execute(
                "SELECT control.bind_shared_egress_profile(%s,%s,%s,%s,%s)",
                (run.tenant_id, run.site_id, operation_id, request_hash, "npm_registry"),
            )
        raise RollbackAdmission()


@pytest.mark.anyio
async def test_astro_observation_checks_blobs_and_missing_registry_is_unavailable(
    admin, identity, scopes, identity_context
):
    import base64
    from urllib.parse import urlsplit

    from signal_core.shared_egress import ProviderEgressResponse

    scope, context = _owner_site(admin, identity, scopes, identity_context)
    target = _target(content_path="src/pages/index.astro")
    binding = _prepare(identity, scope, context, target=target)
    finish_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=binding,
        snapshot=_snapshot(target),
    )
    files = source_files()
    transport, _ = _provider()
    original = transport.provider.request_json.side_effect

    def request_json(**kwargs):
        path = urlsplit(kwargs["url"]).path
        if "/git/trees/" in path:
            data = {
                "sha": "b" * 40,
                "truncated": False,
                "tree": [
                    {"path": path, "mode": "100644", "type": "blob", "sha": _sha(content)}
                    for path, content in files.items()
                ],
            }
        elif "/git/blobs/" in path:
            content = next(
                content for content in files.values() if _sha(content) == path.rsplit("/", 1)[1]
            )
            data = {
                "sha": _sha(content),
                "encoding": "base64",
                "size": len(content),
                "content": base64.b64encode(content).decode(),
            }
        else:
            return original(**kwargs)
        return ProviderEgressResponse(200, "application/json", json.dumps(data).encode())

    transport.provider.request_json.side_effect = request_json
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
    assert extension.framework == "astro" and extension.content_format == "astro"
    assert {path for path, _ in extension.marker_evidence} == {
        "astro.config.mjs",
        "package.json",
        "package-lock.json",
    }
    with pytest.raises(CandidateBuildUnavailable, match="NPM_EGRESS_UNAVAILABLE"):
        await build_candidate_from_github(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            extension_id=extension.id,
            idempotency_key=uuid4(),
            credential=credential,
            github_transport=transport,
            openbao_transport=bao,
            runner=None,
        )
