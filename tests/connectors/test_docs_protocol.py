import json
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx2
import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.docs_protocol import (
    DocsRejected,
    docs_authorization,
    export_text,
    picked_files,
    read_metadata,
)
from signal_core.docs_secrets import OpenBaoDocsSecrets
from signal_core.egress_profiles import DRIVE_METADATA_FIELDS, DrivePickedScope, EgressProfile
from signal_core.gsc_oauth import (
    DRIVE_FILE_SCOPE,
    GscOAuthError,
    exchange_gsc_code,
    refresh_gsc_access_token,
)
from signal_core.gsc_secrets import GscSecretError
from signal_core.shared_egress import ProviderEgressResponse, SharedEgressRequest
from test_gsc_oauth import CLIENT, FakeEgress

FILE = "synthetic-picked-document-123"
ACCESS = "synthetic-docs-access-token-123456"
SCOPE = DrivePickedScope((FILE,), ACCESS)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def tokens(scope=DRIVE_FILE_SCOPE):
    body = {
        "access_token": ACCESS,
        "refresh_token": "synthetic-docs-refresh-token-123456",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    if scope is not None:
        body["scope"] = scope
    return FakeEgress(ProviderEgressResponse(200, "application/json", json.dumps(body).encode()))


def test_picker_has_exact_per_file_scope_offline_pkce_and_no_browser_token():
    authorization = docs_authorization(
        client_id=CLIENT.client_id,
        redirect_uri="https://signal.example.invalid/auth/google-docs/callback",
    )
    query = parse_qs(urlsplit(authorization.url).query)
    assert query["scope"] == [DRIVE_FILE_SCOPE]
    assert query["trigger_onepick"] == ["true"]
    assert query["allow_multiple"] == ["true"]
    assert query["mimetypes"] == ["application/vnd.google-apps.document"]
    assert query["include_granted_scopes"] == ["false"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    assert "access_token" not in query and "refresh_token" not in query
    assert authorization.verifier not in repr(authorization)


@pytest.mark.parametrize(
    "scope",
    [
        None,
        "",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.readonly",
        DRIVE_FILE_SCOPE + " email",
        "read_content",
    ],
)
@pytest.mark.parametrize("refresh", [False, True])
def test_google_docs_exchange_and_refresh_require_verifiable_exact_scope(scope, refresh):
    kwargs = dict(
        egress=tokens(scope), credentials=CLIENT, operation_id=uuid4(), scope=DRIVE_FILE_SCOPE
    )
    with pytest.raises(GscOAuthError) as error:
        if refresh:
            refresh_gsc_access_token(**kwargs, refresh_token="synthetic-docs-refresh-token-123456")
        else:
            exchange_gsc_code(
                **kwargs,
                code="synthetic-docs-code-123456",
                verifier="v" * 43,
                redirect_uri="https://signal.example.invalid/auth/google-docs/callback",
            )
    assert ACCESS not in str(error.value)


@pytest.mark.parametrize(
    "scope",
    [
        "",
        "drive.file",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file email",
    ],
)
def test_closed_google_scope_set_cannot_be_widened(scope):
    with pytest.raises(GscOAuthError):
        exchange_gsc_code(
            egress=tokens(),
            credentials=CLIENT,
            code="synthetic-docs-code-123456",
            verifier="v" * 43,
            redirect_uri="https://signal.example.invalid/callback",
            operation_id=uuid4(),
            scope=scope,
        )


def outbound(profile=EgressProfile.DRIVE_METADATA, **changes):
    kwargs = {
        "method": "GET",
        "url": f"https://www.googleapis.com/drive/v3/files/{FILE}?"
        + urlencode({"fields": DRIVE_METADATA_FIELDS}),
        "headers": (("accept", "application/json"), ("authorization", f"Bearer {ACCESS}")),
        "max_response_bytes": 16384,
        "timeout_seconds": 10,
    }
    if profile == EgressProfile.DRIVE_EXPORT:
        kwargs.update(
            url=f"https://www.googleapis.com/drive/v3/files/{FILE}/export?mimeType=text%2Fplain",
            headers=(("accept", "text/plain"), ("authorization", f"Bearer {ACCESS}")),
            max_response_bytes=512 * 1024,
            accepted_media_types=("text/plain",),
        )
    kwargs.update(changes)
    return EgressHttpRequest(**kwargs)


@pytest.mark.parametrize("profile", [EgressProfile.DRIVE_METADATA, EgressProfile.DRIVE_EXPORT])
def test_drive_profiles_accept_only_selected_exact_gets(profile):
    request = SharedEgressRequest("connector", outbound(profile), profile, drive_picked_scope=SCOPE)
    assert request.credentialed and ACCESS not in repr(request)
    assert len(request.request_sha256) == 32


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD"])
@pytest.mark.parametrize("profile", [EgressProfile.DRIVE_METADATA, EgressProfile.DRIVE_EXPORT])
def test_every_drive_write_and_unqualified_method_rejected(method, profile):
    with pytest.raises(ValueError):
        SharedEgressRequest(
            "connector", outbound(profile, method=method), profile, drive_picked_scope=SCOPE
        )


@pytest.mark.parametrize(
    "changes",
    [
        {
            "url": "https://www.googleapis.com/drive/v3/files/synthetic-unpicked-document?fields="
            + DRIVE_METADATA_FIELDS
        },
        {"url": "https://www.googleapis.com/drive/v3/files?fields=" + DRIVE_METADATA_FIELDS},
        {"url": f"https://docs.googleapis.com/v1/documents/{FILE}"},
        {"url": f"https://www.googleapis.com/drive/v3/files/{FILE}/permissions"},
        {"url": f"https://www.googleapis.com/drive/v3/files/{FILE}?alt=media"},
        {"url": f"https://www.googleapis.com/drive/v3/files/{FILE}?fields=*"},
        {
            "url": f"https://www.googleapis.com/drive/v3/files/{FILE}?"
            + urlencode({"fields": DRIVE_METADATA_FIELDS})
            + "#fragment"
        },
        {
            "headers": (
                ("accept", "application/json"),
                ("authorization", "Bearer synthetic-other-token-123456"),
            )
        },
        {"max_response_bytes": 16385},
        {"timeout_seconds": 11},
        {"body": b"{}"},
    ],
)
def test_profile_path_binding_token_query_size_and_time_negatives(changes):
    with pytest.raises(ValueError):
        SharedEgressRequest(
            "connector", outbound(**changes), EgressProfile.DRIVE_METADATA, drive_picked_scope=SCOPE
        )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "short",
        FILE + "," + FILE,
        "../synthetic-traversal",
        ",".join([FILE + str(n) for n in range(21)]),
    ],
)
def test_picker_manifest_is_bounded_unique_and_path_safe(value):
    with pytest.raises(DocsRejected):
        picked_files(value)


def test_metadata_and_export_are_closed_plaintext_read_profiles():
    data = {
        "id": FILE,
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-10-01T12:00:00Z",
        "version": "1",
        "trashed": False,
        "isAppAuthorized": True,
    }
    egress = FakeEgress(ProviderEgressResponse(200, "application/json", json.dumps(data).encode()))
    assert read_metadata(egress, SCOPE, FILE, uuid4()).version == "1"
    assert egress.requests[-1]["drive_picked_scope"] == SCOPE
    data["isAppAuthorized"] = False
    egress.response = ProviderEgressResponse(200, "application/json", json.dumps(data).encode())
    with pytest.raises(DocsRejected, match="DOCS_RESPONSE_REJECTED"):
        read_metadata(egress, SCOPE, FILE, uuid4())
    egress.response = ProviderEgressResponse(200, "text/plain", b"Owner-selected source text")
    assert export_text(egress, SCOPE, FILE, uuid4()) == b"Owner-selected source text"
    assert egress.requests[-1]["profile"] == EgressProfile.DRIVE_EXPORT


@pytest.mark.parametrize("body", [b"", b"\xff", b"source\x00data", b"x" * (512 * 1024 + 1)])
def test_export_refuses_invalid_or_oversized_text(body):
    with pytest.raises(DocsRejected):
        export_text(
            FakeEgress(ProviderEgressResponse(200, "text/plain", body)), SCOPE, FILE, uuid4()
        )


@pytest.mark.parametrize(
    "reason,code",
    [
        ("insufficientFilePermissions", "DOCS_PROVIDER_UNSHARED"),
        ("appNotAuthorizedToFile", "DOCS_PROVIDER_UNSHARED"),
        ("rateLimitExceeded", "DOCS_PROVIDER_UNAVAILABLE"),
        ("unknown", "DOCS_PROVIDER_UNAVAILABLE"),
    ],
)
def test_only_explicit_unsharing_is_withdrawal_not_quota_failure(reason, code):
    egress = FakeEgress(
        ProviderEgressResponse(
            403,
            "application/json",
            json.dumps({"error": {"errors": [{"reason": reason}]}}).encode(),
        )
    )
    with pytest.raises(DocsRejected, match=code):
        read_metadata(egress, SCOPE, FILE, uuid4())


@pytest.mark.anyio
async def test_docs_openbao_mount_reference_isolation_and_fixed_errors():
    bao = OpenBaoDocsSecrets("https://bao.example.invalid", "synthetic-bao-token-123456")
    identifier = uuid4()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.method == "POST":
            return httpx2.Response(200, json={"data": {"version": 1}})
        return httpx2.Response(403, json={"error": ACCESS})

    transport = httpx2.MockTransport(handler)
    reference = await bao.store_refresh_token(
        identifier, "synthetic-docs-refresh-token-123456", transport=transport
    )
    assert reference == f"secret://google-docs/{identifier}"
    assert calls == [f"/v1/signal-google-docs/data/refresh/{identifier}"]
    with pytest.raises(GscSecretError) as error:
        await bao.refresh_token(reference, transport=transport)
    assert ACCESS not in str(error.value) and ACCESS not in repr(bao)
    with pytest.raises(GscSecretError):
        await bao.refresh_token(f"secret://gsc/{identifier}", transport=transport)
