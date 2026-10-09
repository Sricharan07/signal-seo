import hashlib
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from signal_core.candidate_build import plan_candidate_build
from signal_core.candidate_build_service import CandidateBuildRecord
from signal_core.candidate_sandbox import DockerCandidateSandbox
from signal_core.front_matter_recipes import (
    make_front_matter_recipe_patch,
    verify_front_matter_impact,
)

from tests.tooling.front_matter_support import fixture_source

pytestmark = pytest.mark.skipif(
    os.environ.get("SIGNAL_CANDIDATE_SANDBOX_LAB") != "1", reason="Use the candidate sandbox lab."
)


@pytest.mark.parametrize("framework", ["astro", "eleventy", "nextjs"])
@pytest.mark.parametrize("field", ["title", "description"])
def test_f1_paired_offline_builds_preserve_bom_crlf_content_and_exact_html(framework, field):
    extension, checkout, tarball = fixture_source(framework, crlf=True)
    files = dict(checkout.files)
    path = checkout.inventory.snapshot.content_path
    before = (
        '"Calendar"' if field == "title" else "'Calendar information about the date and the day.'"
    )
    after = (
        '"Calendar Guide"'
        if field == "title"
        else "'Calendar information about the date and the day "
        "with more information about this page.'"
    )
    edit = dict(
        recipe_key="front_matter_" + field,
        path=path,
        offset=files[path].decode().index(before),
        before=before,
        after=after,
    )
    patch = make_front_matter_recipe_patch(files=files, **edit)
    plans = (
        plan_candidate_build(extension, checkout, front_matter_recipe={}),
        plan_candidate_build(
            extension,
            checkout,
            patch=patch.patch,
            approved_paths=frozenset({path}),
            front_matter_recipe=edit,
        ),
    )
    records = []
    for plan in plans:
        key = (
            hashlib.sha256(plan.dependency_manifest.tarballs[0].integrity.encode()).hexdigest()
            + ".tgz"
        )
        result = DockerCandidateSandbox(timeout_seconds=25).run(
            replace(plan, dependencies=((key, tarball),))
        )
        assert result.exit_class == "passed" and len(result.built_html) == 2
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
    target = plans[0].artifact_root + "/index.html"
    impact = verify_front_matter_impact(
        files=files,
        path=path,
        recipe_key=edit["recipe_key"],
        baseline=records[0],
        candidate=records[1],
        expected_by_page={target: after[1:-1]},
        page_urls={target: "https://example.invalid/"},
    )
    assert impact.approval_class == "A2" and impact.pages == (target,)
