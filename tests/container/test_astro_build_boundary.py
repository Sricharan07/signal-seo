import hashlib
import os
from dataclasses import replace

import pytest
from signal_core.candidate_build import plan_candidate_build
from signal_core.candidate_sandbox import DockerCandidateSandbox

from tests.tooling.astro_build_support import astro_source, package_tarball
from tests.tooling.test_indexnow_placement import KEY

pytestmark = pytest.mark.skipif(
    os.environ.get("SIGNAL_CANDIDATE_SANDBOX_LAB") != "1",
    reason="Run through scripts/run-candidate-sandbox-tests.py.",
)


def _plan(*, extra=None, script=None):
    tarball = package_tarball(**({"script": script} if script else {}))
    extension, checkout = astro_source(tarball=tarball, extra=extra)
    plan = plan_candidate_build(extension, checkout)
    item = plan.dependency_manifest.tarballs[0]
    key = hashlib.sha256(item.integrity.encode()).hexdigest() + ".tgz"
    return replace(plan, dependencies=((key, tarball),))


@pytest.mark.parametrize("public", ["public", "static/assets"])
def test_indexnow_key_is_present_at_site_root_after_real_offline_build(public):
    script = (
        b"#!/usr/bin/env node\nconst fs=require('fs');fs.mkdirSync('public-build');"
        b"fs.writeFileSync('public-build/index.html',"
        b"'<!doctype html><title>Synthetic page</title>');"
        + f"fs.cpSync('{public}', 'public-build', {{recursive:true}});".encode()
    )
    tarball = package_tarball(script=script)
    extension, checkout = astro_source(
        tarball=tarball,
        config=f"export default {{outDir:'./public-build',publicDir:'./{public}'}};".encode(),
    )
    path = public + "/" + KEY + ".txt"
    plan = plan_candidate_build(
        extension,
        checkout,
        patch={path: KEY.encode()},
        approved_paths=frozenset({path}),
        indexnow_key=KEY,
    )
    item = plan.dependency_manifest.tarballs[0]
    cache_key = hashlib.sha256(item.integrity.encode()).hexdigest() + ".tgz"
    result = DockerCandidateSandbox(timeout_seconds=25).run(
        replace(plan, dependencies=((cache_key, tarball),))
    )
    assert result.exit_class == "passed" and result.exit_code == 0
    assert (
        "public-build/" + KEY + ".txt",
        hashlib.sha256(KEY.encode()).hexdigest(),
        len(KEY),
    ) in result.artifacts
    assert len(result.artifacts) == 2


def test_real_npm_offline_install_and_build_disable_every_lifecycle_script():
    plan = _plan()
    result = DockerCandidateSandbox(timeout_seconds=25).run(plan)
    assert result.exit_class == "passed"
    assert result.exit_code == 0
    assert result.lockfile_sha256 == plan.dependency_manifest.lockfile_sha256
    assert result.built_pages == (
        ("public-build/index.html", result.artifacts[0][1], "Synthetic page", "Bounded build"),
    )
    assert len(result.artifacts) == 1
    assert result.unavailable_reason is None


def test_build_that_requires_disabled_lifecycle_script_is_explicitly_unavailable():
    result = DockerCandidateSandbox(timeout_seconds=25).run(
        _plan(extra={"needs-lifecycle": b"required"})
    )
    assert result.exit_class == "crash"
    assert not result.artifacts and not result.built_pages
    assert result.unavailable_reason == "ASTRO_BUILD_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED"


def test_offline_build_cannot_modify_source_or_workflows():
    plan = _plan(extra={"malicious-build": b"attempt", ".github/workflows/ci.yml": b"safe"})
    result = DockerCandidateSandbox(timeout_seconds=25).run(plan)
    assert result.exit_class == "policy_rejected"
    assert result.unavailable_reason == "ASTRO_BUILT_OUTPUT_REJECTED"
    assert not result.artifacts


def test_offline_installed_executable_cannot_reach_network():
    script = (
        b"#!/usr/bin/env node\nconst net=require('net'); const s=net.connect(80,'192.0.2.1');"
        b"s.on('error',()=>process.exit(33)); setTimeout(()=>process.exit(34),2000);"
    )
    result = DockerCandidateSandbox(timeout_seconds=25).run(_plan(script=script))
    assert result.exit_class == "crash" and result.exit_code in (33, 34)
    assert not result.artifacts
