import hashlib
import json
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from signal_core.astro_recipe_service import (
    seal_astro_recipe_revision,
    seal_front_matter_recipe_revision,
    seal_nextjs_recipe_revision,
)
from signal_core.astro_recipes import astro_recipe_release_manifest
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.front_matter_recipes import front_matter_recipe_release_manifest
from signal_core.nextjs_recipes import nextjs_recipe_release_manifest
from signal_core.recipe_releases import RecipeReleaseUnavailable, RecipeVersion
from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

from tests.tooling.astro_delivery_support import fixture_source, record
from tests.tooling.front_matter_support import fixture_source as front_matter_source
from tests.tooling.nextjs_support import fixture_source as nextjs_source


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "adapter,framework",
    [
        ("astro", "astro"),
        ("front_matter", "astro"),
        ("front_matter", "eleventy"),
        ("front_matter", "nextjs"),
        ("nextjs_metadata", "nextjs"),
    ],
)
@pytest.mark.parametrize(
    "failure", [None, "release", "source", "crawl", "build", "outside", "path", "mapping"]
)
async def test_service_pairs_only_offline_build_ports_and_seals_exact_current_scope(
    monkeypatch, failure, tmp_path, adapter, framework
):
    import signal_core.astro_recipe_service as service
    import signal_core.technical_recipe_service as technical

    extension, checkout, _ = fixture_source()
    path = "src/pages/dates/[slug].astro"
    source = dict(checkout.files)[path].decode()
    edit = dict(
        path=path,
        offset=source.index('"Calendar"'),
        before='"Calendar"',
        after="`Calendar ${date}`",
    )
    key = "astro_title"
    root = "public-build"
    if adapter == "front_matter":
        extension, checkout, _ = front_matter_source(framework)
        path = checkout.inventory.snapshot.content_path
        source = dict(checkout.files)[path].decode()
        edit = dict(
            path=path,
            offset=source.index('"Calendar"'),
            before='"Calendar"',
            after='"Calendar Guide"',
        )
        key = "front_matter_title"
        root = {"astro": "public-build", "eleventy": "_site", "nextjs": "out"}[framework]
    if adapter == "nextjs_metadata":
        extension, checkout, _ = nextjs_source(shared=True)
        path = checkout.inventory.snapshot.content_path
        source = dict(checkout.files)[path].decode()
        edit = dict(
            path=path,
            offset=source.index('"Calendar"'),
            before='"Calendar"',
            after='"Calendar Guide"',
        )
        key = "nextjs_title"
        root = "out"
    html = {
        f"public-build/dates/{date}/index.html": (
            f"<html><head><title>Calendar</title></head><body>Calendar {date}</body></html>"
        )
        for date in ("2026-01-01", "2026-01-02", "2026-01-03")
    }
    html["public-build/index.html"] = "<title>Unchanged</title>"
    expected = {p: "Calendar " + p.split("/")[2] for p in html if "/dates/" in p}
    if adapter in {"front_matter", "nextjs_metadata"}:
        html = {
            p.replace("public-build", root): s.replace("<body>", "<body>Guide ")
            for p, s in html.items()
        }
        expected = {p.replace("public-build", root): "Calendar Guide" for p in expected}
    if adapter == "nextjs_metadata":

        def flat(p):
            return p.replace("/index.html", ".html") if "/dates/" in p else p

        html = {flat(p): s for p, s in html.items()}
        expected = {flat(p): s for p, s in expected.items()}
    changed = {
        p: s.replace("<title>Calendar</title>", f"<title>{expected[p]}</title>")
        if p in expected
        else s
        for p, s in html.items()
    }
    page = next(iter(expected))
    evidence = dict(
        manifest_id=str(uuid4()),
        manifest_sha256="b" * 64,
        page_id=str(uuid4()),
        page_url="https://example.invalid/dates/2026-01-01"
        + ("" if adapter == "nextjs_metadata" else "/"),
        site_origin="https://example.invalid",
        page_body_sha256=hashlib.sha256(html[page].encode()).hexdigest(),
        finding={"id": str(uuid4()), "key": "metadata.title.duplicate"},
    )
    release_id = uuid4()
    release = SimpleNamespace(
        version=RecipeVersion(1, 0, 0),
        content_hash="c" * 64,
        manifest=(
            nextjs_recipe_release_manifest
            if adapter == "nextjs_metadata"
            else front_matter_recipe_release_manifest
            if adapter == "front_matter"
            else astro_recipe_release_manifest
        )(key, release_id),
    )
    if failure == "release":
        release.manifest = dict(release.manifest, autonomy_eligible=True)
    if failure == "crawl":
        evidence["page_body_sha256"] = "0" * 64
    if failure == "path":
        edit["path"] = ".github/workflows/deploy.yml"
    if failure == "mapping":
        expected = {"outside/index.html": "Calendar"}
    monkeypatch.setattr(service, "get_reviewed_recipe_release", lambda *args, **kwargs: release)
    monkeypatch.setattr(technical, "_load_evidence", lambda *args: evidence)
    monkeypatch.setattr(service, "_clean_transaction", lambda connection: nullcontext())
    monkeypatch.setattr(service, "GitHubSharedEgressTransport", type("Transport", (), {}))
    monkeypatch.setattr(service, "read_github_pr_extension", lambda *args, **kwargs: extension)
    monkeypatch.setattr(
        service,
        "read_github_read_binding",
        lambda *args, **kwargs: SimpleNamespace(target=object()),
    )

    async def current(*args, **kwargs):
        snapshot = checkout.inventory.snapshot
        return replace(snapshot, base_sha="0" * 40) if failure == "source" else snapshot

    async def source_port(*args, **kwargs):
        return checkout

    calls = []
    runner, registry, cache = object(), object(), object()
    store = EncryptedLocalArtifactStore(tmp_path.resolve() / "artifacts")
    artifact_key = ArtifactEncryptionKey("synthetic-candidate-artifact-key/v1", b"k" * 32)

    async def build_port(*args, **kwargs):
        calls.append(kwargs)
        assert (
            kwargs["runner"] is runner
            and kwargs["registry_provider"] is registry
            and kwargs["registry_cache"] is cache
        )
        assert kwargs["artifact_store"] is store and kwargs["artifact_key"] is artifact_key
        result = record(html if len(calls) == 1 else changed)
        if len(calls) == 2 and failure == "build":
            result = replace(result, exit_class="crash")
        if len(calls) == 2 and failure == "outside":
            result = record(dict(changed, **{root + "/index.html": "Changed"}))
        return result

    monkeypatch.setattr(service, "inspect_current_github_pr_extension", current)
    monkeypatch.setattr(service, "checkout_github_repository", source_port)
    monkeypatch.setattr(service, "build_candidate_from_github", build_port)
    sealed = []

    class Connection:
        def execute(self, statement, args):
            sealed.append(args)
            return SimpleNamespace(fetchone=lambda: (args[3], args[12], False, "sealed"))

    async def credentials(**kwargs):
        return object()

    request = dict(
        session_token="synthetic-" + "a" * 33,
        current_recovery_generation="test-generation-1",
        site_id=extension.site_id,
        extension_id=extension.id,
        report_id=uuid4(),
        finding_id=uuid4(),
        recipe_key=key,
        recipe_release_id=release_id,
        idempotency_key=uuid4(),
        build_idempotency_key=uuid4(),
        credential=SimpleNamespace(credentials=credentials),
        github_transport=service.GitHubSharedEgressTransport(),
        runner=runner,
        edit=edit,
        expected_by_page=expected,
        registry_provider=registry,
        registry_cache=cache,
        artifact_store=store,
        artifact_key=artifact_key,
    )
    seal = (
        seal_nextjs_recipe_revision
        if adapter == "nextjs_metadata"
        else seal_front_matter_recipe_revision
        if adapter == "front_matter"
        else seal_astro_recipe_revision
    )
    if failure:
        with pytest.raises(
            RecipeReleaseUnavailable if failure == "release" else TechnicalRecipeUnavailable
        ):
            await seal(Connection(), Connection(), **request)
        assert not sealed
    else:
        await seal(Connection(), Connection(), **request)
        assert (
            len(calls) == 2
            and "patch" not in calls[0]
            and calls[1][
                "nextjs_recipe"
                if adapter == "nextjs_metadata"
                else "front_matter_recipe"
                if adapter == "front_matter"
                else "astro_recipe"
            ]["path"]
            == path
        )
        assert calls[0]["idempotency_key"] != calls[1]["idempotency_key"]
        manifest = json.loads(sealed[0][11])
        assert manifest["approval_class"] == "A4" and manifest["built_impact"]["page_count"] == 3
        assert manifest["built_impact"]["scope_sha256"] == hashlib.sha256(sealed[0][14]).hexdigest()
