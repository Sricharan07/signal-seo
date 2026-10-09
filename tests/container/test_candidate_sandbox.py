import hashlib
import os
from dataclasses import replace

import pytest
from signal_core.candidate_build import plan_candidate_build
from signal_core.candidate_sandbox import DockerCandidateSandbox, _create_args

from tests.tooling.test_candidate_build import _source

pytestmark = pytest.mark.skipif(
    os.environ.get("SIGNAL_CANDIDATE_SANDBOX_LAB") != "1",
    reason="Run through scripts/run-candidate-sandbox-tests.py.",
)


def _plan(script):
    extension, checkout = _source(extra={"build.js": script.encode()})
    return plan_candidate_build(extension, checkout)


def test_container_config_has_no_network_mount_credentials_or_root():
    args = _create_args("signal-candidate-test")
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--user") + 1] == "10001:10001"
    assert "--read-only" in args
    assert "--cap-drop" in args and "ALL" in args
    assert "--memory" in args and "--cpus" in args and "--pids-limit" in args
    assert "--mount" not in args and "--volume" not in args and "-v" not in args
    assert all("TOKEN" not in value and "SECRET" not in value for value in args)


def test_real_container_builds_bounded_artifact_and_cleans_up():
    plan = _plan(
        "const fs=require('fs'); fs.mkdirSync('.next'); fs.writeFileSync('.next/index.html','ok');"
    )
    result = DockerCandidateSandbox(timeout_seconds=10).run(plan)
    assert result.exit_class == "passed"
    assert result.exit_code == 0
    assert result.artifacts == ((".next/index.html", hashlib.sha256(b"ok").hexdigest(), 2),)


def test_indexnow_root_key_is_exact_in_static_output():
    key = "a" * 64
    path = key + ".txt"
    script = (
        f"const fs=require('fs'); fs.mkdirSync('_site'); fs.copyFileSync('{path}','_site/{path}');"
    )
    extension, checkout = _source(extra={"build.js": script.encode()}, framework="eleventy")
    extension = replace(extension, content_format="html")
    plan = plan_candidate_build(
        extension, checkout, patch={path: key.encode()}, approved_paths=frozenset({path})
    )
    result = DockerCandidateSandbox(timeout_seconds=10).run(plan)
    assert result.exit_class == "passed"
    assert result.artifacts == (("_site/" + path, hashlib.sha256(key.encode()).hexdigest(), 64),)


def test_untrusted_script_cannot_see_host_socket_mount_or_signal_credential():
    plan = _plan(
        "const fs=require('fs'); "
        "if(fs.existsSync('/var/run/docker.sock') || fs.existsSync('/run/secrets') || "
        "Object.keys(process.env).some(k=>k.startsWith('SIGNAL_'))) process.exit(41); "
        "fs.mkdirSync('.next'); fs.writeFileSync('.next/index.html','isolated');"
    )
    result = DockerCandidateSandbox(timeout_seconds=10).run(plan)
    assert result.exit_class == "passed"
    assert result.artifacts == ((".next/index.html", hashlib.sha256(b"isolated").hexdigest(), 8),)


@pytest.mark.parametrize(
    "script,expected",
    [
        ("process.stdout.write('x'.repeat(100000));", "output_limit"),
        ("while(true){}", "timeout"),
        ("process.exit(42)", "crash"),
        (
            "const net=require('net'); const s=net.connect(80,'192.0.2.1'); "
            "s.on('error',()=>process.exit(33)); setTimeout(()=>process.exit(0),2000);",
            "crash",
        ),
        (
            "const fs=require('fs'); fs.mkdirSync('.next'); "
            "fs.symlinkSync('/etc/passwd','.next/leak');",
            "policy_rejected",
        ),
        (
            "const fs=require('fs'); fs.writeFileSync('.github/workflows/ci.yml','changed'); "
            "fs.mkdirSync('.next'); fs.writeFileSync('.next/index.html','ok');",
            "policy_rejected",
        ),
    ],
)
def test_real_sandbox_failure_and_malicious_build_cases(script, expected):
    extra = {}
    if ".github/workflows" in script:
        extra[".github/workflows/ci.yml"] = b"safe\n"
    extension, checkout = _source(extra={"build.js": script.encode(), **extra})
    plan = plan_candidate_build(extension, checkout)
    result = DockerCandidateSandbox(timeout_seconds=2).run(plan)
    assert result.exit_class == expected
    assert not result.artifacts


def test_real_memory_limit_classifies_oom():
    plan = _plan(
        "const a=[]; while(true){const b=Buffer.alloc(16*1024*1024); b.fill(1); a.push(b)}"
    )
    result = DockerCandidateSandbox(timeout_seconds=10).run(plan)
    assert result.exit_class == "oom"
    assert not result.artifacts


def test_grounded_json_ld_exact_block_and_other_built_artifacts_unchanged():
    from dataclasses import replace

    from signal_core.structured_data_recipe import (
        assert_structured_build,
        make_structured_data_patch,
    )

    from tests.tooling.test_technical_seo_recipes import _case, _sha

    source = "<html><head><title>Guide</title></head><body><h1>Guide</h1></body></html>"
    extension, checkout, evidence = _case(source, "metadata.title.missing")
    files = dict(checkout.files)
    files["build.js"] = (
        b"const fs=require('fs');fs.mkdirSync('_site');"
        b"fs.copyFileSync('index.html','_site/index.html');"
        b"fs.writeFileSync('_site/unchanged.txt','unchanged');"
    )
    inventory = replace(
        checkout.inventory,
        entries=tuple(
            replace(entry, sha=_sha(files[entry.path])) for entry in checkout.inventory.entries
        ),
    )
    checkout = replace(checkout, files=tuple(sorted(files.items())), inventory=inventory)
    patch, _ = make_structured_data_patch(
        evidence=evidence, extension=extension, checkout=checkout, proposal={"@type": "Article"}
    )
    runner = DockerCandidateSandbox(timeout_seconds=10)
    baseline = runner.run(plan_candidate_build(extension, checkout))
    candidate = runner.run(
        plan_candidate_build(
            extension,
            checkout,
            patch={patch.path: patch.after},
            approved_paths=frozenset({patch.path}),
        )
    )
    assert_structured_build(patch, baseline, candidate, output_path="_site/index.html")
