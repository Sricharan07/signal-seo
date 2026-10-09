import hashlib
import os
from dataclasses import replace

import pytest
from signal_core.astro_recipes import make_astro_recipe_patch, verify_astro_impact
from signal_core.candidate_build import plan_candidate_build
from signal_core.candidate_build_service import CandidateBuildRecord
from signal_core.candidate_sandbox import DockerCandidateSandbox

from tests.tooling.astro_delivery_support import fixture_source

pytestmark = pytest.mark.skipif(
    os.environ.get("SIGNAL_CANDIDATE_SANDBOX_LAB") != "1", reason="Use the candidate sandbox lab."
)


@pytest.mark.parametrize(
    "path,count,approval",
    [
        ("src/pages/index.astro", 1, "A2"),
        ("src/pages/dates/[slug].astro", 3, "A4"),
        ("src/content/blog/guide.json", 1, "A2"),
    ],
)
def test_offline_pair_builds_generated_scope_and_exact_html(path, count, approval):
    extension, checkout, tarball = fixture_source()
    files = dict(checkout.files)
    source = files[path].decode()
    edit = dict(
        recipe_key="astro_title",
        path=path,
        offset=source.index('"Calendar"'),
        before='"Calendar"',
        after="`Calendar ${date}`" if "[slug]" in path else '"Calendar Guide"',
    )
    patch = make_astro_recipe_patch(files=files, **edit)
    sandbox = DockerCandidateSandbox(timeout_seconds=25)
    baseline = plan_candidate_build(extension, checkout)
    candidate = plan_candidate_build(
        extension, checkout, patch=patch.patch, approved_paths=frozenset({path}), astro_recipe=edit
    )
    key = (
        hashlib.sha256(baseline.dependency_manifest.tarballs[0].integrity.encode()).hexdigest()
        + ".tgz"
    )
    records = []
    for plan in (baseline, candidate):
        result = sandbox.run(replace(plan, dependencies=((key, tarball),)))
        assert (
            result.exit_class == "passed"
            and len(result.built_pages) == 5
            and len(result.built_html) == 5
        )
        from uuid import uuid4

        records.append(
            CandidateBuildRecord(
                uuid4(),
                uuid4(),
                "completed",
                plan.base_sha,
                plan.patch_sha256,
                plan.toolchain,
                "npm run build",
                exit_class=result.exit_class,
                artifacts=result.artifacts,
                lockfile_sha256=result.lockfile_sha256,
                built_pages=result.built_pages,
                built_html=result.built_html,
            )
        )
    before, after = map(lambda r: dict(r.built_html), records)
    targets = {
        p: ("Calendar " + p.split("/")[2] if "/dates/" in p else "Calendar Guide")
        for p in before
        if before[p] != after[p]
    }
    impact = verify_astro_impact(
        files=files,
        path=path,
        recipe_key="astro_title",
        baseline=records[0],
        candidate=records[1],
        expected_by_page=targets,
        page_urls={p: "https://example.invalid/" for p in targets},
    )
    assert len(impact.pages) == count and impact.approval_class == approval
