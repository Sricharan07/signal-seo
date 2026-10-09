import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import httpx2
import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import EgressProfile, profile_headers
from signal_core.indexnow_protocol import (
    INDEXNOW_ENDPOINT,
    IndexNowSubmitScope,
    generate_indexnow_key,
    key_file_outcome,
    valid_indexnow_key,
)
from signal_core.indexnow_secrets import IndexNowSecretUnavailable, OpenBaoIndexNowKeys
from signal_core.recipe_autonomy import AUTONOMY_RECIPES, attest_recipe_autonomy
from signal_core.recipe_releases import RecipeReleaseUnavailable
from signal_core.shared_egress import SharedEgressRequest
from signal_core.technical_seo_recipes import technical_recipe_release_manifest


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_keys_are_format_valid_unique_and_never_autonomous():
    keys = {generate_indexnow_key() for _ in range(100)}
    assert len(keys) == 100 and all(valid_indexnow_key(k) and len(k) == 64 for k in keys)
    manifest = technical_recipe_release_manifest("technical_indexnow_key", uuid4())
    assert manifest["approval_class"] == "owner_review"
    assert "technical_indexnow_key" not in AUTONOMY_RECIPES
    with pytest.raises(RecipeReleaseUnavailable):
        attest_recipe_autonomy(
            None,
            release_id=uuid4(),
            recipe_key="technical_indexnow_key",
            actor_user_id=uuid4(),
            attestation_id=uuid4(),
        )


@pytest.mark.parametrize(
    "key", ["short", "x" * 129, "synthetic/key", "synthetic key", "synthetic-key\n"]
)
def test_bad_key_format(key):
    assert not valid_indexnow_key(key)


def scope():
    return IndexNowSubmitScope(
        "https://site.example.invalid",
        "synthetic-indexnow-key",
        ("https://site.example.invalid/changed",),
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example.invalid/",
        "http://site.example.invalid/",
        "https://site.example.invalid:444/",
        "https://site.example.invalid/#fragment",
        "https://site.example.invalid/user/../page",
        "https://user@site.example.invalid/",
    ],
)
def test_wrong_site_rejected_before_io(url):
    with pytest.raises(ValueError):
        IndexNowSubmitScope(scope().verified_origin, scope().key, (url,))


@pytest.mark.parametrize(
    "status,media,body,final,outcome,reason",
    [
        (200, "text/plain", b"synthetic-indexnow-key", None, "fetched", "KEY_DEPLOYED"),
        (404, "text/plain", b"", None, "fetched", "EC_142_KEY_MISSING"),
        (200, "text/plain", b"wrong", None, "fetched", "EC_142_KEY_MISMATCH"),
        (200, "text/plain", b"synthetic-indexnow-key\n", None, "fetched", "EC_142_KEY_MISMATCH"),
        (200, "text/html", b"synthetic-indexnow-key", None, "fetched", "EC_142_KEY_CONTENT_TYPE"),
        (
            200,
            "text/plain",
            b"synthetic-indexnow-key",
            "https://other.example.invalid/key.txt",
            "fetched",
            "EC_142_KEY_REDIRECT",
        ),
        (None, None, b"", None, "transport_error", "EC_142_KEY_UNREACHABLE"),
    ],
)
def test_exact_key_file(status, media, body, final, outcome, reason):
    assert (
        key_file_outcome(
            scope(),
            status=status,
            media_type=media,
            body=body,
            final_url=final or scope().key_location,
            outcome=outcome,
        )
        == reason
    )


def outbound():
    s = scope()
    return SharedEgressRequest(
        "connector",
        EgressHttpRequest(
            "POST",
            INDEXNOW_ENDPOINT,
            headers=profile_headers(EgressProfile.INDEXNOW_SUBMIT, "POST", None),
            body=s.body(),
            accepted_media_types=("application/json", "text/plain"),
            max_response_bytes=4096,
            timeout_seconds=5,
        ),
        EgressProfile.INDEXNOW_SUBMIT,
        sensitive_body=True,
        indexnow_scope=s,
    )


def test_closed_submission_is_sensitive_and_exact():
    request = outbound()
    assert request.credentialed
    assert json.loads(request.http.body) == scope().document()
    assert request.body_sha256 == hashlib.sha256(scope().body()).digest()


@pytest.mark.parametrize(
    "mutation",
    ["origin", "method", "json", "size", "time", "headers", "url", "scope", "other_urls"],
)
def test_profile_negatives(mutation):
    r = outbound()
    changes = {
        "origin": {"url": "https://other.example.invalid/indexnow"},
        "method": {"method": "GET"},
        "json": {"body": b"not json"},
        "size": {"body": b" " * 65537},
        "time": {"timeout_seconds": 6},
        "headers": {"headers": (("accept", "application/json"), ("content-type", "text/plain"))},
        "url": {"url": INDEXNOW_ENDPOINT + "?extra=1"},
        "other_urls": {
            "body": json.dumps(
                {**scope().document(), "urlList": ["https://other.example.invalid/"]}
            ).encode()
        },
    }
    with pytest.raises(ValueError):
        replace(r, indexnow_scope=None) if mutation == "scope" else replace(
            r, http=replace(r.http, **changes[mutation])
        )


class BaoDouble:
    def __init__(self):
        self.documents = {}
        self.writes = 0

    def handle(self, request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["options"] == {"cas": 0}
            assert set(body["data"]) == {"key"}
            if request.url.path in self.documents:
                return httpx2.Response(400, json={})
            self.documents[request.url.path] = body["data"]
            self.writes += 1
            return httpx2.Response(200, json={"data": {"version": 1}})
        data = self.documents.get(request.url.path)
        return (
            httpx2.Response(404, json={})
            if data is None
            else httpx2.Response(
                200,
                json={
                    "data": {
                        "data": data,
                        "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                    }
                },
            )
        )


@pytest.mark.anyio
async def test_openbao_only_cas_create_and_replay():
    double = BaoDouble()
    keys = OpenBaoIndexNowKeys("https://bao.example.invalid", "synthetic-indexnow-bao-token")
    args = dict(
        tenant_id=uuid4(),
        site_id=uuid4(),
        key_id=uuid4(),
        transport=httpx2.MockTransport(double.handle),
    )
    key = await keys.key(**args, create=True)
    assert await keys.key(**args, create=True) == key and double.writes == 1
    assert valid_indexnow_key(key)
    assert key not in repr(keys)
    with pytest.raises(IndexNowSecretUnavailable):
        await keys.key(**{**args, "key_id": uuid4()})


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["error", "wrong_type", "destroyed", "version", "data", "tls"])
async def test_openbao_fail_closed(kind):
    document = {
        "data": {
            "data": {"key": "synthetic-indexnow-key"},
            "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
        }
    }
    if kind == "destroyed":
        document["data"]["metadata"]["destroyed"] = True
    if kind == "version":
        document["data"]["metadata"]["version"] = 2
    if kind == "data":
        document["data"]["data"]["extra"] = "untrusted"

    def handle(request):
        if kind == "error":
            return httpx2.Response(500, json={"error": "private"})
        if kind == "wrong_type":
            return httpx2.Response(200, text="not json")
        return httpx2.Response(200, json=document)

    with pytest.raises(IndexNowSecretUnavailable):
        await OpenBaoIndexNowKeys(
            "https://bao.example.invalid", "synthetic-indexnow-bao-token"
        ).key(
            tenant_id=uuid4(),
            site_id=uuid4(),
            key_id=uuid4(),
            transport=httpx2.MockTransport(handle),
            verify=False if kind == "tls" else True,
        )
