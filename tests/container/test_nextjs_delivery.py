import hashlib
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from signal_core.candidate_build import plan_candidate_build
from signal_core.candidate_build_service import CandidateBuildRecord
from signal_core.candidate_sandbox import DockerCandidateSandbox
from signal_core.nextjs_recipes import make_nextjs_recipe_patch, verify_nextjs_impact

from tests.tooling.nextjs_support import edit, fixture_source

pytestmark = pytest.mark.skipif(
    os.environ.get("SIGNAL_CANDIDATE_SANDBOX_LAB") != "1", reason="Use the candidate sandbox lab."
)


@pytest.mark.parametrize("router,shared", [("app", False), ("pages", False), ("app", True)])
@pytest.mark.parametrize("field", ["title", "description"])
def test_f2_literal_offline_pair_has_exact_built_html_and_a4_shared_scope(router, shared, field):
    extension, checkout, tarball = fixture_source(router, shared=shared)
    files = dict(checkout.files)
    path = checkout.inventory.snapshot.content_path
    request = edit(files, path, field)
    patch = make_nextjs_recipe_patch(files=files, **request)
    plans = (
        plan_candidate_build(extension, checkout, nextjs_recipe={}),
        plan_candidate_build(
            extension,
            checkout,
            nextjs_recipe=request,
            patch=patch.patch,
            approved_paths=frozenset({path}),
        ),
    )
    records = []
    for plan in plans:
        key = (
            hashlib.sha256(plan.dependency_manifest.tarballs[0].integrity.encode()).hexdigest()
            + ".tgz"
        )
        result = DockerCandidateSandbox(timeout_seconds=30).run(
            replace(plan, dependencies=((key, tarball),))
        )
        assert result.exit_class == "passed" and result.unavailable_reason is None
        records.append(
            CandidateBuildRecord(
                uuid4(),
                extension.id,
                "completed",
                plan.base_sha,
                plan.patch_sha256,
                plan.toolchain,
                "npm run build",
                exit_class=result.exit_class,
                artifacts=result.artifacts,
                lockfile_sha256=result.lockfile_sha256,
                built_html=result.built_html,
            )
        )
    pages = {p: request["after"][1:-1] for p, html in records[0].built_html if "untouched" not in p}
    impact = verify_nextjs_impact(
        files=files,
        path=path,
        recipe_key=request["recipe_key"],
        baseline=records[0],
        candidate=records[1],
        expected_by_page=pages,
        page_urls={p: "https://example.invalid/" for p in pages},
    )
    assert impact.approval_class == ("A4" if shared else "A2")
    assert len(impact.pages) == (3 if shared else 1)


def test_network_needing_next_build_is_unavailable_without_network_or_telemetry():
    extension, checkout, tarball = fixture_source(network=True)
    plan = plan_candidate_build(extension, checkout, nextjs_recipe={})
    key = (
        hashlib.sha256(plan.dependency_manifest.tarballs[0].integrity.encode()).hexdigest() + ".tgz"
    )
    result = DockerCandidateSandbox(timeout_seconds=25).run(
        replace(plan, dependencies=((key, tarball),))
    )
    assert result.exit_class == "crash"
    assert result.unavailable_reason == "NEXT_BUILD_NETWORK_UNAVAILABLE"
    assert not result.artifacts and not result.built_html
