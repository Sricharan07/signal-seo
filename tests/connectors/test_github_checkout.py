import base64
import hashlib
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from joserfc.jwk import RSAKey
from signal_core.github_app import (
    GitHubAppCredentials,
    GitHubAppProtocolError,
    GitHubRepositoryTarget,
    checkout_github_repository,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _sha(content):
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


def _provider(*, corrupt=False, symlink=False, truncated=False):
    blobs = {
        "next.config.mjs": b"export default {};\n",
        "app/page.tsx": b"export default function Page() { return 'ok'; }\n",
    }
    by_sha = {_sha(content): content for content in blobs.values()}
    paths = []

    def handler(request):
        path = request.url.path
        paths.append((request.method, str(request.url)))
        if path.endswith("/access_tokens"):
            status = 201
            document = {
                "token": "t" * 32,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "permissions": {"contents": "read", "metadata": "read"},
                "repository_selection": "selected",
                "repositories": [{"id": 245, "full_name": "SignalOwner/website"}],
            }
        elif path.endswith("/branches/main"):
            status = 200
            document = {"name": "main", "commit": {"sha": "a" * 40}, "protected": True}
        elif "/git/commits/" in path:
            status = 200
            document = {"sha": "a" * 40, "tree": {"sha": "b" * 40}}
        elif "/git/trees/" in path:
            status = 200
            document = {
                "sha": "b" * 40,
                "truncated": truncated,
                "tree": [
                    {
                        "path": name,
                        "type": "blob",
                        "mode": "120000" if symlink else "100644",
                        "sha": _sha(content),
                    }
                    for name, content in blobs.items()
                ],
            }
        elif "/git/blobs/" in path:
            status = 200
            sha = path.rsplit("/", 1)[-1]
            content = by_sha[sha]
            document = {
                "sha": sha,
                "size": len(content),
                "encoding": "base64",
                "content": base64.b64encode(content + (b"bad" if corrupt else b"")).decode(),
            }
        else:
            status = 200
            document = {
                "id": 245,
                "full_name": "SignalOwner/website",
                "private": True,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
            }
        return httpx2.Response(status, json=document)

    return httpx2.MockTransport(handler), paths, blobs


@pytest.mark.anyio
async def test_exact_commit_checkout_reads_verified_blobs_only():
    transport, paths, blobs = _provider()
    key = RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
    checkout = await checkout_github_repository(
        credentials=GitHubAppCredentials(123, key.as_pem(private=True).decode()),
        target=GitHubRepositoryTarget(1234, "SignalOwner", "website", "main", "app/page.tsx"),
        transport=transport,
    )
    assert checkout.inventory.snapshot.base_sha == "a" * 40
    assert dict(checkout.files) == blobs
    assert len(paths) == 7
    assert [method for method, _ in paths] == ["POST", "GET", "GET", "GET", "GET", "GET", "GET"]
    assert all("/pulls" not in path for _, path in paths)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change,code",
    [
        ({"corrupt": True}, "GITHUB_PROVIDER_RESPONSE_REJECTED"),
        ({"symlink": True}, "GITHUB_REPOSITORY_TYPE_REJECTED"),
        ({"truncated": True}, "GITHUB_REPOSITORY_SIZE_REJECTED"),
    ],
)
async def test_corrupt_blob_symlink_or_partial_tree_never_enters_checkout(change, code):
    transport, paths, _ = _provider(**change)
    key = RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
    with pytest.raises(GitHubAppProtocolError, match=code):
        await checkout_github_repository(
            credentials=GitHubAppCredentials(123, key.as_pem(private=True).decode()),
            target=GitHubRepositoryTarget(1234, "SignalOwner", "website", "main", "app/page.tsx"),
            transport=transport,
        )
    if change.get("symlink") or change.get("truncated"):
        assert all("/git/blobs/" not in path for _, path in paths)
