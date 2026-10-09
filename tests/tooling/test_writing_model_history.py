from datetime import UTC, datetime
from uuid import uuid4

import pytest
from signal_api.proposal_contracts import ModelProposalRecord, VerifiedModelProposalRecord
from signal_core.model_reasoning import MetadataDraft, ModelUsage
from signal_core.proposals import (
    FixtureModelRun,
    _valid_model_manifest,
    _valid_verified_model_manifest,
    build_model_fixture_manifest,
    build_verified_homepage_model_manifest,
)


@pytest.mark.parametrize("verified", [False, True])
def test_historical_luna_v1_is_readable_but_cannot_masquerade_as_current_release(verified):
    run = FixtureModelRun(
        agent_run_id=uuid4(),
        model_call_id=uuid4(),
        attempt_number=1,
        finding_id=uuid4(),
        evidence_id=uuid4(),
        command_id=uuid4(),
        manifest_id=uuid4(),
        resource_locator="https://example.invalid/"
        if verified
        else "/fixture/missing-meta-description",
        confidence_class="deterministic",
        evidence_observed_at=datetime(2026, 9, 1, tzinfo=UTC),
        input_sha256="a" * 64,
        status="requested",
        reused=False,
        outcome="started",
    )
    draft = MetadataDraft(
        meta_description=(
            "Read a synthetic founder guide with clear facts and useful next decisions."
        ),
        rationale="Only the synthetic page facts are described.",
        provider_response_id="resp_synthetic_history",
        model_reported="gpt-6-luna",
        usage=ModelUsage(120, 40, 160, 0),
        output_sha256="b" * 64,
    )
    builder = build_verified_homepage_model_manifest if verified else build_model_fixture_manifest
    validate = _valid_verified_model_manifest if verified else _valid_model_manifest
    manifest = builder(site_id=uuid4(), run=run, draft=draft)
    assert validate(manifest)
    old_release = ("verified" if verified else "local") + "-gpt-5.6-luna-metadata-v1"
    manifest["model"].update(
        release=old_release,
        model_requested="gpt-5.6-luna",
        model_reported="gpt-5.6-luna",
        prompt_sha256="f963221dfbac7cc218bead459e6f11ca61d07e41a98ae8fa46d784f5d06b9347"
        if verified
        else "6c50fef0b4d7c0f096b34a2cd7f52de346e205bae3d788a7f25a7ddce73746e0",
    )
    manifest["role_contributions"][1]["release"] = old_release
    assert validate(manifest)
    contract = VerifiedModelProposalRecord if verified else ModelProposalRecord
    assert contract.model_validate(manifest["model"]).release == old_release
    assert manifest["authority"]["external_write"] is False
    manifest["model"]["prompt_sha256"] = "c" * 64
    assert not validate(manifest)
