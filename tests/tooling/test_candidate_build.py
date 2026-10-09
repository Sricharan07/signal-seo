import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from signal_core.candidate_build import (
    EMPTY_PATCH_SHA256,
    CandidatePolicyRejected,
    plan_candidate_build,
)
from signal_core.github_app import (
    GitHubRepositoryCheckout,
    GitHubRepositoryInventory,
    GitHubRepositorySnapshot,
    GitHubTreeEntry,
)
from signal_core.github_pr_extension import GitHubPrExtension


def _sha(content):
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


def _source(*, extra=None, framework="nextjs", content_path="app/page.tsx"):
    files = {
        "next.config.mjs": b"export default {};\n",
        "package.json": json.dumps({"scripts": {"build": "node build.js"}}).encode(),
        "build.js": (
            b"require('fs').mkdirSync('.next'); "
            b"require('fs').writeFileSync('.next/index.html','ok');\n"
        ),
        "app/page.tsx": b"export default function Page() { return 'ok'; }\n",
    }
    files.update(extra or {})
    snapshot = GitHubRepositorySnapshot(
        123,
        245,
        "SignalOwner",
        "website",
        "SignalOwner/website",
        True,
        "main",
        "main",
        "a" * 40,
        True,
        content_path,
        datetime.now(UTC),
    )
    entries = tuple(
        GitHubTreeEntry(path, "100644", "blob", _sha(content))
        for path, content in sorted(files.items())
    )
    checkout = GitHubRepositoryCheckout(
        GitHubRepositoryInventory(snapshot, "b" * 40, entries, False),
        tuple(sorted(files.items())),
    )
    extension = GitHubPrExtension(
        uuid4(),
        uuid4(),
        uuid4(),
        "observed",
        repository_id=245,
        base_sha="a" * 40,
        tree_sha="b" * 40,
        framework=framework,
        content_format="tsx",
        coverage="complete",
        content_sha=_sha(files[content_path]),
    )
    return extension, checkout


def test_baseline_plan_binds_exact_source_without_patch():
    extension, checkout = _source()
    plan = plan_candidate_build(extension, checkout)
    assert plan.base_sha == "a" * 40
    assert plan.patch_sha256 == EMPTY_PATCH_SHA256
    assert plan.command == ("npm", "run", "build")
    assert plan.artifact_root == ".next"
    assert dict(plan.files)["app/page.tsx"] == b"export default function Page() { return 'ok'; }\n"


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "../escape",
        ".env",
        "package-lock.json",
        "package.json",
        "deploy/config.yml",
        "src/secret.key",
        "auth/login.ts",
    ],
)
def test_protected_or_out_of_scope_patch_is_rejected_before_build(path):
    extension, checkout = _source()
    with pytest.raises(CandidatePolicyRejected, match="CANDIDATE_PATCH_REJECTED"):
        plan_candidate_build(
            extension,
            checkout,
            patch={path: b"changed\n"},
            approved_paths=frozenset({path}),
        )


def test_exact_recipe_scope_and_text_patch_are_required():
    extension, checkout = _source()
    path = "app/page.tsx"
    with pytest.raises(CandidatePolicyRejected, match="CANDIDATE_PATCH_REJECTED"):
        plan_candidate_build(extension, checkout, patch={path: b"new\n"})
    with pytest.raises(CandidatePolicyRejected, match="CANDIDATE_PATCH_REJECTED"):
        plan_candidate_build(
            extension,
            checkout,
            patch={path: b"\x00binary"},
            approved_paths=frozenset({path}),
        )
    plan = plan_candidate_build(
        extension,
        checkout,
        patch={path: b"new\n"},
        approved_paths=frozenset({path}),
    )
    assert plan.patch_sha256 != EMPTY_PATCH_SHA256
    assert dict(plan.files)[path] == b"new\n"


def test_changed_tree_missing_content_and_symlink_like_mode_fail_closed():
    extension, checkout = _source()
    changed = GitHubRepositoryCheckout(
        GitHubRepositoryInventory(
            checkout.inventory.snapshot, "c" * 40, checkout.inventory.entries, False
        ),
        checkout.files,
    )
    with pytest.raises(CandidatePolicyRejected, match="CANDIDATE_SOURCE_CHANGED"):
        plan_candidate_build(extension, changed)
    entries = tuple(
        GitHubTreeEntry(
            entry.path,
            "120000" if entry.path == "app/page.tsx" else entry.mode,
            entry.kind,
            entry.sha,
        )
        for entry in checkout.inventory.entries
    )
    symlink = GitHubRepositoryCheckout(
        GitHubRepositoryInventory(checkout.inventory.snapshot, "b" * 40, entries, False),
        checkout.files,
    )
    with pytest.raises(CandidatePolicyRejected, match="CANDIDATE_CHECKOUT_INVALID"):
        plan_candidate_build(extension, symlink)


def test_unknown_build_profile_and_prebuild_script_are_unavailable():
    extension, checkout = _source(framework="hugo")
    with pytest.raises(CandidatePolicyRejected, match="CANDIDATE_TOOLCHAIN_UNAVAILABLE"):
        plan_candidate_build(extension, checkout)
    package = json.dumps(
        {"scripts": {"prebuild": "curl https://bad", "build": "node build.js"}}
    ).encode()
    extension, checkout = _source(extra={"package.json": package})
    with pytest.raises(CandidatePolicyRejected, match="CANDIDATE_BUILD_COMMAND_UNAVAILABLE"):
        plan_candidate_build(extension, checkout)
