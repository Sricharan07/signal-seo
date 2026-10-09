import hashlib
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
import rfc8785
from signal_core.candidate_build import plan_candidate_build
from signal_core.github_app import GitHubRepositoryCheckout
from signal_core.github_pr_patch import (
    GitHubPrPatchRejected,
    git_commit_sha,
    git_tree_sha,
    plan_github_pr_patch,
)
from signal_core.github_pr_provider import GitHubWriteEgressTransport
from signal_core.shared_egress import SharedEgressProvider
from test_candidate_build import _source


def _fixture(*, path="app/page.tsx", before="ok", after="better", prefix=b""):
    extension, checkout = _source()
    if prefix:
        extension, checkout = _source(extra={path: prefix + dict(checkout.files)[path]})
    tree = git_tree_sha(dict(checkout.files))
    inventory = replace(checkout.inventory, tree_sha=tree)
    checkout = GitHubRepositoryCheckout(inventory, checkout.files)
    extension = replace(extension, tree_sha=tree)
    source = dict(checkout.files)[path]
    text = source.decode()
    position = text.index(before)
    offset = len(text[:position].encode())
    result = (text[:position] + after + text[position + len(before) :]).encode()
    plan = plan_candidate_build(
        extension, checkout, patch={path: result}, approved_paths=frozenset({path})
    )
    manifest = {
        "schema_version": 1,
        "site_id": str(extension.site_id),
        "extension_id": str(extension.id),
        "base_sha": extension.base_sha,
        "source_path": path,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "result_sha256": hashlib.sha256(result).hexdigest(),
        "patch_sha256": plan.patch_sha256,
        "patch": {"offset": offset, "before": before, "after": after},
        "finding_id": str(uuid4()),
        "evidence": {"finding": {"key": "metadata.title.missing"}},
        "build_receipt": {"exit_class": "passed", "artifacts": [{"path": "x"}]},
        "recovery_plan": "Revert the exact source patch.",
        "approval_class": "owner_review",
    }
    body = rfc8785.dumps(manifest)
    operation_id = uuid4()
    created_at = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    return extension, checkout, manifest, body, operation_id, created_at


def test_git_tree_and_commit_hashes_match_git(tmp_path):
    files = {"index.html": b"home\n", "pages/about.html": b"about\n"}
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    for path, content in files.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    result = subprocess.run(
        ["git", "write-tree"], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    assert git_tree_sha(files) == result.stdout.strip()
    assert (
        len(
            git_commit_sha(
                tree_sha=result.stdout.strip(),
                base_sha="a" * 40,
                message="Fix\n",
                created_at=datetime(2026, 9, 29, tzinfo=UTC),
            )
        )
        == 40
    )


def test_sealed_one_file_patch_reconstructs_exact_candidate_tree():
    extension, checkout, _, body, operation_id, created_at = _fixture()
    revision_sha = hashlib.sha256(body).hexdigest()
    patch = plan_github_pr_patch(
        manifest_bytes=body,
        revision_sha256=revision_sha,
        operation_id=operation_id,
        created_at=created_at,
        extension=extension,
        checkout=checkout,
    )
    assert patch.content.endswith(b"'better'; }\n")
    assert patch.tree_sha == git_tree_sha({**dict(checkout.files), patch.path: patch.content})
    assert patch.commit_sha == git_commit_sha(
        tree_sha=patch.tree_sha,
        base_sha=extension.base_sha,
        message=patch.commit_message,
        created_at=created_at,
    )


def test_patch_offsets_are_utf8_bytes_not_codepoints():
    extension, checkout, _, body, operation_id, created_at = _fixture(prefix=b"// caf\xc3\xa9\n")
    patch = plan_github_pr_patch(
        manifest_bytes=body,
        revision_sha256=hashlib.sha256(body).hexdigest(),
        operation_id=operation_id,
        created_at=created_at,
        extension=extension,
        checkout=checkout,
    )
    assert patch.content.startswith(b"// caf\xc3\xa9\n")
    assert patch.content.endswith(b"'better'; }\n")


def test_indexnow_key_adds_only_the_reviewed_root_file():
    extension, checkout, manifest, _, operation_id, created_at = _fixture()
    key = "a" * 64
    path = key + ".txt"
    plan = plan_candidate_build(
        extension, checkout, patch={path: key.encode()}, approved_paths=frozenset({path})
    )
    manifest.update(
        audit_report_id=None,
        source_path=path,
        source_sha256=hashlib.sha256(b"").hexdigest(),
        result_sha256=hashlib.sha256(key.encode()).hexdigest(),
        patch_sha256=plan.patch_sha256,
        patch={"offset": 0, "before": "", "after": key},
        evidence={"finding": {"key": "indexnow.key.required"}},
    )
    body = rfc8785.dumps(manifest)
    patch = plan_github_pr_patch(
        manifest_bytes=body,
        revision_sha256=hashlib.sha256(body).hexdigest(),
        operation_id=operation_id,
        created_at=created_at,
        extension=extension,
        checkout=checkout,
    )
    assert (patch.path, patch.content) == (path, key.encode())
    assert patch.tree_sha == git_tree_sha({**dict(checkout.files), path: key.encode()})
    manifest["source_path"] = "nested/" + path
    body = rfc8785.dumps(manifest)
    with pytest.raises(GitHubPrPatchRejected):
        plan_github_pr_patch(
            manifest_bytes=body,
            revision_sha256=hashlib.sha256(body).hexdigest(),
            operation_id=operation_id,
            created_at=created_at,
            extension=extension,
            checkout=checkout,
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data["patch"].update({"offset": 999}),
        lambda data: data.update({"source_path": ".github/workflows/ci.yml"}),
        lambda data: data.update({"source_sha256": "0" * 64}),
        lambda data: data.update({"result_sha256": "0" * 64}),
        lambda data: data.update({"patch_sha256": "0" * 64}),
        lambda data: data.update({"approval_class": "automatic"}),
        lambda data: data.update({"evidence": {}}),
    ],
)
def test_patch_drift_protected_path_and_missing_evidence_fail_closed(change):
    extension, checkout, manifest, _, operation_id, created_at = _fixture()
    change(manifest)
    body = rfc8785.dumps(manifest)
    with pytest.raises(GitHubPrPatchRejected):
        plan_github_pr_patch(
            manifest_bytes=body,
            revision_sha256=hashlib.sha256(body).hexdigest(),
            operation_id=operation_id,
            created_at=created_at,
            extension=extension,
            checkout=checkout,
        )


def test_changed_base_tree_and_unsupported_framework_fail_closed():
    extension, checkout, _, body, operation_id, created_at = _fixture()
    for modified_extension in (
        replace(extension, tree_sha="f" * 40),
        replace(extension, framework="unknown"),
    ):
        with pytest.raises(GitHubPrPatchRejected):
            plan_github_pr_patch(
                manifest_bytes=body,
                revision_sha256=hashlib.sha256(body).hexdigest(),
                operation_id=operation_id,
                created_at=created_at,
                extension=modified_extension,
                checkout=checkout,
            )


def test_write_egress_rejects_default_branch_and_unsealed_body():
    extension, checkout, _, body, operation_id, created_at = _fixture()
    patch = plan_github_pr_patch(
        manifest_bytes=body,
        revision_sha256=hashlib.sha256(body).hexdigest(),
        operation_id=operation_id,
        created_at=created_at,
        extension=extension,
        checkout=checkout,
    )
    provider = SharedEgressProvider(
        None,
        None,
        None,
        None,
        SimpleNamespace(
            allowed_origins=("https://api.github.com",),
            user_agent="SignalBot/1.0 (+https://signal.example/bot)",
            max_redirects=0,
            max_body_bytes=256 * 1024,
            request_timeout_seconds=5,
        ),
        None,
        "github-pr-test",
        None,
        None,
        "connector",
    )
    target = checkout.inventory.snapshot
    from signal_core.github_app import GitHubRepositoryTarget

    transport = GitHubWriteEgressTransport(
        provider,
        GitHubRepositoryTarget(
            target.installation_id,
            target.owner,
            target.repository,
            target.base_branch,
            target.content_path,
        ),
        lambda step: None,
        operation_id=operation_id,
        patch=patch,
        base_sha=target.base_sha,
        base_tree_sha=checkout.inventory.tree_sha,
        pr_body="Exact review body",
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + "t" * 32,
        "User-Agent": "Signal-SEO-Agent/0.0.0",
        "X-GitHub-Api-Version": "2026-03-10",
        "Content-Type": "application/json",
    }
    url = "https://api.github.com/repos/SignalOwner/website/git/refs"
    approved = httpx2.Request(
        "POST",
        url,
        headers=headers,
        json={"ref": f"refs/heads/signal/{operation_id.hex}", "sha": patch.commit_sha},
    )
    assert transport._route(approved) == "branch"
    for request in (
        httpx2.Request(
            "POST", url, headers=headers, json={"ref": "refs/heads/main", "sha": patch.commit_sha}
        ),
        httpx2.Request(
            "POST",
            url,
            headers=headers,
            json={"ref": f"refs/heads/signal/{operation_id.hex}", "sha": "0" * 40},
        ),
        httpx2.Request(
            "POST",
            "https://api.github.com/repos/SignalOwner/website/actions/secrets",
            headers=headers,
            json={},
        ),
        httpx2.Request(
            "POST",
            "https://api.github.com/repos/SignalOwner/other/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/signal/{operation_id.hex}", "sha": patch.commit_sha},
        ),
        httpx2.Request(
            "POST",
            "https://api.github.com/app/installations/999/access_tokens",
            headers=headers,
            json={
                "repositories": ["website"],
                "permissions": {"contents": "write", "pull_requests": "write"},
            },
        ),
    ):
        with pytest.raises(httpx2.ConnectError):
            transport._route(request)
