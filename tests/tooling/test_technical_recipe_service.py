import hashlib
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from signal_core.candidate_build import plan_candidate_build
from signal_core.candidate_build_service import CandidateBuildRecord, CandidateBuildUnavailable
from signal_core.recipe_releases import RecipeReleaseUnavailable, RecipeVersion
from signal_core.technical_recipe_service import seal_technical_recipe_revision
from signal_core.technical_seo_recipes import (
    TechnicalRecipeUnavailable,
    technical_recipe_release_manifest,
)
from test_technical_seo_recipes import _case


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Connection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, args):
        self.calls.append((statement, args))
        return SimpleNamespace(fetchone=lambda: (uuid4(), args[-1], False, "sealed"))


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [None, "build", "lost", "model", "release"])
async def test_service_seals_only_passed_current_reviewed_build(monkeypatch, failure):
    import signal_core.technical_recipe_service as service

    extension, checkout, evidence = _case(
        "<head></head><h1>Signal Guide</h1>", "metadata.title.missing"
    )
    evidence.update(
        {
            "manifest_id": str(uuid4()),
            "manifest_sha256": "b" * 64,
            "page_id": str(uuid4()),
        }
    )
    release_id = uuid4()
    release = SimpleNamespace(
        id=release_id,
        version=RecipeVersion(1, 0, 0),
        content_hash="c" * 64,
        manifest=technical_recipe_release_manifest("technical_title", release_id),
    )
    monkeypatch.setattr(service, "_clean_transaction", lambda connection: nullcontext())
    monkeypatch.setattr(service, "_load_evidence", lambda *args: evidence)
    monkeypatch.setattr(service, "GitHubSharedEgressTransport", type("Transport", (), {}))
    monkeypatch.setattr(service, "read_github_pr_extension", lambda *args, **kwargs: extension)
    monkeypatch.setattr(
        service,
        "read_github_read_binding",
        lambda *args, **kwargs: SimpleNamespace(target=object()),
    )

    async def current(*args, **kwargs):
        return checkout.inventory.snapshot

    async def source(*args, **kwargs):
        return checkout

    async def build(*args, **kwargs):
        if failure == "lost":
            raise CandidateBuildUnavailable("CANDIDATE_DISPATCH_UNKNOWN")
        plan = plan_candidate_build(
            extension, checkout, patch=kwargs["patch"], approved_paths=kwargs["approved_paths"]
        )
        return CandidateBuildRecord(
            uuid4(),
            extension.site_id,
            "completed",
            plan.base_sha,
            plan.patch_sha256,
            plan.toolchain,
            "npm run build",
            exit_class="crash" if failure == "build" else "passed",
            exit_code=1 if failure == "build" else 0,
            logs_sha256="d" * 64,
            log_bytes=1,
            artifacts=(("_site/index.html", "e" * 64, 1),),
        )

    monkeypatch.setattr(service, "inspect_current_github_pr_extension", current)
    monkeypatch.setattr(service, "checkout_github_repository", source)
    monkeypatch.setattr(service, "build_candidate_from_github", build)
    monkeypatch.setattr(
        service,
        "get_reviewed_recipe_release",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(RecipeReleaseUnavailable("revoked"))
            if failure == "release"
            else release
        ),
    )
    model = (
        None
        if failure == "model"
        else SimpleNamespace(draft_crawl_text=lambda task: _draft("Signal Guide"))
    )
    connection = Connection()
    kwargs = {
        "session_token": "a" * 43,
        "current_recovery_generation": "test-generation-1",
        "site_id": extension.site_id,
        "extension_id": extension.id,
        "report_id": uuid4(),
        "finding_id": uuid4(),
        "recipe_key": "technical_title",
        "recipe_release_id": release_id,
        "idempotency_key": uuid4(),
        "build_idempotency_key": uuid4(),
        "credential": SimpleNamespace(credentials=lambda **kwargs: _draft(object())),
        "github_transport": service.GitHubSharedEgressTransport(),
        "runner": object(),
        "model": model,
    }
    if failure == "release":
        expected = RecipeReleaseUnavailable
    elif failure == "lost":
        expected = CandidateBuildUnavailable
    elif failure:
        expected = TechnicalRecipeUnavailable
    else:
        expected = None
    if expected:
        with pytest.raises(expected):
            await seal_technical_recipe_revision(connection, connection, **kwargs)
        assert not connection.calls
    else:
        sealed = await seal_technical_recipe_revision(connection, connection, **kwargs)
        assert len(connection.calls) == 1
        canonical = connection.calls[0][1][-2]
        assert sealed.revision_sha256 == hashlib.sha256(canonical).hexdigest()
        assert b'"approval_class":"owner_review"' in canonical


async def _draft(text):
    return SimpleNamespace(
        text=text,
        provider_response_id="resp_synthetic_1",
        model_reported="gpt-6-luna",
        usage=SimpleNamespace(
            input_tokens=1, output_tokens=1, total_tokens=2, cached_input_tokens=0
        ),
    )
