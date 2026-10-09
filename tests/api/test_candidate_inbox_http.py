from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.candidate_recipe_inbox import CandidateRecipeInboxItem

TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.test"
SITE_ID = UUID("11111111-1111-4111-8111-111111111111")


@pytest.mark.parametrize(
    "negative", [None, "autonomy", "offsite", "stuffing", "audit", "cap", "extra"]
)
def test_internal_link_manifest_is_closed_existing_text_owner_review(negative):
    from pydantic import ValidationError
    from signal_api.candidate_inbox_contracts import CandidateRecipeManifest

    manifest = item().manifest
    manifest["audit_report_id"] = None
    manifest["patch"] = {
        "offset": 20,
        "before": "Garden irrigation",
        "after": '<a href="https://example.test/water.html">Garden irrigation</a>',
    }
    manifest["evidence"]["site_origin"] = "https://example.test"
    manifest["evidence"]["finding"]["key"] = "links.internal.add"
    manifest["internal_link"] = dict(
        recipe_key="technical_internal_link_add",
        page_cap=3,
        autonomy_eligible=False,
        baseline_build_id=str(uuid4()),
        output_path="_site/index.html",
        target_id=str(uuid4()),
        target_url="https://example.test/water.html",
        graph={},
        coverage="partial",
    )
    if negative == "autonomy":
        manifest["internal_link"]["autonomy_eligible"] = True
    elif negative == "offsite":
        manifest["internal_link"]["target_url"] = "https://other.example.invalid/water.html"
    elif negative == "stuffing":
        manifest["patch"]["after"] += " invented anchor"
    elif negative == "audit":
        manifest["audit_report_id"] = str(uuid4())
    elif negative == "cap":
        manifest["internal_link"]["page_cap"] = 4
    elif negative == "extra":
        manifest["internal_link"]["grant"] = True
    if negative:
        with pytest.raises(ValidationError):
            CandidateRecipeManifest.model_validate(manifest)
    else:
        assert (
            CandidateRecipeManifest.model_validate(manifest).internal_link.autonomy_eligible
            is False
        )


def item(*, status: str = "pending") -> CandidateRecipeInboxItem:
    now = datetime.now(UTC).replace(microsecond=0)
    return CandidateRecipeInboxItem(
        revision_id=uuid4(),
        revision_sha256="a" * 64,
        manifest={
            "schema_version": 1,
            "site_id": str(SITE_ID),
            "extension_id": str(uuid4()),
            "build_id": str(uuid4()),
            "audit_report_id": str(uuid4()),
            "finding_id": str(uuid4()),
            "recipe_release_id": str(uuid4()),
            "release_content_hash": "b" * 64,
            "base_sha": "c" * 40,
            "patch_sha256": "d" * 64,
            "source_path": "index.html",
            "source_sha256": "e" * 64,
            "result_sha256": "f" * 64,
            "patch": {"offset": 0, "before": "old", "after": "new"},
            "evidence": {
                "manifest_id": str(uuid4()),
                "manifest_sha256": "1" * 64,
                "page_id": str(uuid4()),
                "page_url": "https://example.test/",
                "finding": {
                    "id": str(uuid4()),
                    "title": "Missing title",
                    "summary": "No title was found.",
                    "resource_locator": "https://example.test/",
                },
            },
            "build_receipt": {
                "toolchain": "node",
                "command": "npm run build",
                "exit_class": "passed",
                "logs_sha256": "2" * 64,
                "artifacts": [{"path": "_site/index.html", "sha256": "3" * 64, "size": 3}],
            },
            "expected_impact": "A title becomes available.",
            "recovery_plan": "Revert this exact patch.",
            "approval_class": "owner_review",
            "claim_review_required": False,
            "model_draft": None,
        },
        sealed_at=now,
        recipe_release_id=uuid4(),
        release_content_hash="b" * 64,
        base_sha="c" * 40,
        patch_sha256="d" * 64,
        review_status=status,  # type: ignore[arg-type]
        decision_id=None,
        decision=None,
        decided_by_user_id=None,
        decision_channel=None,
        decided_at=None,
    )


@dataclass
class StubCandidateInbox:
    current: CandidateRecipeInboxItem
    reads: int = 0

    async def candidate_recipe_inbox(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[CandidateRecipeInboxItem, ...]:
        assert session_token == TOKEN and site_id == SITE_ID
        self.reads += 1
        return (self.current,)

    async def decide_candidate_recipe_revision(self, **_kwargs) -> CandidateRecipeInboxItem:
        return self.current


@pytest.mark.anyio
async def test_candidate_inbox_exposes_only_one_strict_sealed_revision():
    gateway = StubCandidateInbox(item())
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=BrowserSecurity(
            csrf_hmac_key=b"candidate-inbox-browser-security-key",
            allowed_origins=frozenset({ORIGIN}),
        ),
        browser_candidate_inbox=gateway,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get(
            f"/v1/sites/{SITE_ID}/candidate-recipe-inbox",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["revisions"][0]["revision_sha256"] == "a" * 64
    assert body["revisions"][0]["manifest"]["patch"] == {
        "offset": 0,
        "before": "old",
        "after": "new",
    }
    assert gateway.reads == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "framework,adapter",
    [
        ("astro", None),
        ("astro", "front_matter"),
        ("eleventy", "front_matter"),
        ("nextjs", "front_matter"),
        ("nextjs", "nextjs_metadata"),
    ],
)
async def test_astro_inbox_exposes_exact_impact_and_rejects_forged_count(framework, adapter):
    current = item()
    current.manifest.update(
        framework=framework,
        recipe_key="nextjs_title"
        if adapter == "nextjs_metadata"
        else "front_matter_title"
        if adapter
        else "astro_title",
        approval_class="A4",
        autonomy_eligible=False,
        built_impact={
            "baseline_build_id": str(uuid4()),
            "lockfile_sha256": "4" * 64,
            "scope_sha256": "5" * 64,
            "page_count": 2,
            "pages": ["dist/dates/one/index.html", "dist/dates/two/index.html"],
            "samples": [{"path": "dist/dates/one/index.html", "before": "", "after": "Calendar"}],
        },
    )
    if adapter:
        current.manifest.update(
            content_adapter=adapter, claim_review_required=True, model_draft=None
        )
    current.manifest["evidence"]["site_origin"] = "https://example.test"
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_candidate_inbox=StubCandidateInbox(current),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get(
            f"/v1/sites/{SITE_ID}/candidate-recipe-inbox",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
        assert response.status_code == 200
        manifest = response.json()["revisions"][0]["manifest"]
        assert manifest["approval_class"] == "A4" and manifest["built_impact"]["page_count"] == 2
        current.manifest["built_impact"]["page_count"] = 1
        response = await api.get(
            f"/v1/sites/{SITE_ID}/candidate-recipe-inbox",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
        assert response.status_code == 500
