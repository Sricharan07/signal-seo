import hashlib
import os
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx2
import psycopg
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_api.webflow_owner import ComposedWebflowGateway, WebflowOwnerEgressFactory
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_http import EgressHttpResult
from signal_core.session_tokens import hash_session_token
from signal_core.webflow import WebflowUnavailable

from tests.webflow.test_webflow import MAPPING, WebflowDouble, service, source

ORIGIN = "https://dashboard.example.invalid"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change", ["stale_mfa", "future_mfa", "password", "unapproved", "wrong_site", "wrong_digest"]
)
async def test_current_fresh_owner_and_approved_exact_source(
    admin, api, identity, scopes, identity_context, change
):
    scope, common, origin, candidate, digest = await source(
        admin, api, identity, scopes, identity_context, approved=change != "unapproved"
    )
    svc, bao = service(api, common)
    if change == "future_mfa":
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                "UPDATE app.sessions SET auth_time=clock_timestamp()+interval '1 minute' "
                "WHERE session_token_hash=%s",
                (hash_session_token(common["session_token"]),),
            )
        return
    if change in {"stale_mfa", "future_mfa"}:
        interval = "- interval '6 minutes'" if change == "stale_mfa" else "+ interval '1 minute'"
        admin.execute(
            "UPDATE app.sessions SET auth_time=clock_timestamp() "
            + interval
            + " WHERE session_token_hash=%s",
            (hash_session_token(common["session_token"]),),
        )
    if change == "password":
        admin.execute(
            "UPDATE app.sessions SET mfa_level='primary' WHERE session_token_hash=%s",
            (hash_session_token(common["session_token"]),),
        )
    args = {
        "session_token": common["session_token"],
        "site_id": scopes[1].site_id if change == "wrong_site" else scope.site_id,
    }
    if change in {"stale_mfa", "future_mfa", "password", "wrong_site"}:
        with pytest.raises(WebflowUnavailable):
            await svc.begin(**args, provider_site="a" * 24, collection_id="b" * 24)
        assert list(bao.documents) == ["client"]
    else:
        assert svc.call(common["session_token"], scope.site_id, "options")["articles"] == (
            []
            if change == "unapproved"
            else [
                {
                    "candidate_id": str(candidate),
                    "source_sha256": digest,
                    "title": "Founders are our audience.",
                }
            ]
        )
        with pytest.raises(WebflowUnavailable):
            svc.seal(
                **args,
                binding_id=uuid4(),
                candidate_id=candidate,
                source_sha256="0" * 64 if change == "wrong_digest" else digest,
            )


@pytest.mark.parametrize(
    "profile,method,url,allowed",
    [
        ("webflow_oauth", "POST", "https://api.webflow.com/oauth/access_token", True),
        ("webflow_oauth", "GET", "https://api.webflow.com/oauth/access_token", False),
        ("webflow_revoke", "POST", "https://webflow.com/oauth/revoke_authorization", True),
        ("webflow_revoke", "POST", "https://webflow.com/oauth/revoke_authorization?extra=1", False),
        ("webflow", "GET", "https://api.webflow.com/v2/sites/" + "a" * 24 + "/collections", True),
        (
            "webflow",
            "POST",
            "https://api.webflow.com/v2/collections/" + "b" * 24 + "/items/insert",
            False,
        ),
        ("webflow", "GET", "https://api.webflow.com/v2/collections/" + "b" * 24 + "/items", False),
        ("webflow", "GET", "https://evil.example.invalid/v2/token/introspect", False),
    ],
)
def test_owner_egress_additions_are_closed(admin, profile, method, url, allowed):
    assert admin.execute(
        "SELECT control.owner_connector_request_allowed(%s,%s,%s)", (profile, method, url)
    ).fetchone() == (allowed,)


@pytest.mark.anyio
@pytest.mark.parametrize("revoke_gateway_failure", [False, True])
async def test_owner_http_oauth_mapping_seal_review_revoke_without_cms_write(
    admin, api, identity, scopes, identity_context, tmp_path, monkeypatch, revoke_gateway_failure
):
    from signal_api.integration_connectors import OwnerOnboardingProvider

    failures = []

    class RecordedProvider(OwnerOnboardingProvider):
        def request_json(self, **kwargs):
            try:
                return super().request_json(**kwargs)
            except Exception as error:
                failures.append(getattr(error, "code", type(error).__name__))
                raise

    monkeypatch.setattr("signal_api.webflow_owner.OwnerOnboardingProvider", RecordedProvider)
    scope, common, origin, candidate, digest = await source(
        admin, api, identity, scopes, identity_context
    )
    svc, bao = service(api, common)
    double = WebflowDouble(origin, "a" * 24, "b" * 24)
    double.resolved_address = "93.184.216.34"

    class Fetcher:
        def request(self, request, *, policy):
            if request.url.endswith("/robots.txt"):
                body = b"User-agent: *\nAllow: /\n"
                return EgressHttpResult(
                    1,
                    request.url,
                    request.url,
                    request.method,
                    "fetched",
                    200,
                    "text/plain",
                    (),
                    "93.184.216.34",
                    body,
                    hashlib.sha256(body).hexdigest(),
                    len(body),
                    1,
                )
            if request.url.endswith("/oauth/revoke_authorization"):
                double.calls.append((request.method, request.url, request.body))
                body = b'{"did_revoke":true}'
                return EgressHttpResult(
                    1,
                    request.url,
                    request.url,
                    request.method,
                    "fetched",
                    200,
                    "application/json",
                    (),
                    "93.184.216.34",
                    body,
                    hashlib.sha256(body).hexdigest(),
                    len(body),
                    1,
                )
            return double.request(request, policy=policy)

    @contextmanager
    def database():
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            yield connection

    @contextmanager
    def writer_database():
        yield api

    class Recovery:
        async def current_generation(self):
            return SimpleNamespace(value=common["generation"])

    egress = WebflowOwnerEgressFactory(
        database,
        EncryptedLocalArtifactStore(tmp_path / "owner-artifacts"),
        ArtifactEncryptionKey("artifact-key:v1:publishing-owner-test", b"K" * 32),
        Fetcher(),
    )

    @contextmanager
    def owner_egress(token, site, generation, target):
        if revoke_gateway_failure and target == "https://webflow.com":
            raise RuntimeError("synthetic-private-egress-failure")
        with egress(token, site, generation, target) as provider:
            yield provider

    gateway = ComposedWebflowGateway(
        writer_database, Recovery(), owner_egress, svc.secrets_store, ORIGIN, svc.openbao_options
    )
    security = BrowserSecurity(b"synthetic-publishing-owner-csrf-key", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_webflow=gateway
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={common['session_token']}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(common["session_token"]),
    }
    path = f"/v1/sites/{scope.site_id}/webflow"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=ORIGIN
    ) as client:

        async def post(action, values):
            r = await client.post(
                path + "/" + action, json={"schema_version": 1, **values}, headers=headers
            )
            assert r.status_code == 200, (
                r.text,
                failures,
                [(method, url) for method, url, _ in double.calls],
            )
            return r.json()

        begun = await post(
            "begin", {"provider_site": double.site, "collection_id": double.collection}
        )
        state = httpx2.URL(begun["authorization_url"]).params["state"]
        values = {
            "attempt_id": begun["attempt_id"],
            "state": state,
            "code": "synthetic-owner-code",
            "field_mapping": MAPPING,
        }
        bound = await post("complete", values)
        assert (
            await client.post(
                path + "/complete", json={"schema_version": 1, **values}, headers=headers
            )
        ).status_code == 403
        options = (await client.get(path + "/options", headers=headers)).json()
        assert options["articles"][0]["candidate_id"] == str(candidate)
        sealed = await post(
            "seal",
            {"binding_id": bound["id"], "candidate_id": str(candidate), "source_sha256": digest},
        )
        assert (
            await post(
                "seal",
                {
                    "binding_id": bound["id"],
                    "candidate_id": str(candidate),
                    "source_sha256": digest,
                },
            )
        )["state"] == "replayed"
        revision = (await client.get(path, headers=headers)).json()["inbox"][0]
        await post(
            "review",
            {
                "id": sealed["id"],
                "revision_sha256": revision["revision_sha256"],
                "decision": "approved",
            },
        )
        revoked = await post("revoke", {"binding_id": bound["id"]})
        assert revoked["state"] == "AUTHORITY_DURABILITY_PENDING"
        assert revoked["upstream"] == ("NOT_EXECUTED" if revoke_gateway_failure else "accepted")
        assert list(bao.documents) == ["client"]
        assert not any("/items" in url for _, url, _ in double.calls)
        assert (
            await client.post(
                path + "/seal",
                json={
                    "schema_version": 1,
                    "binding_id": bound["id"],
                    "candidate_id": str(candidate),
                    "source_sha256": digest,
                },
                headers=headers,
            )
        ).status_code == 403
        with pytest.raises(WebflowUnavailable, match="WEBFLOW_LIVE_QUALIFICATION_NOT_EXECUTED"):
            await replace(svc, disposable_test_writes=False).deliver(
                session_token=common["session_token"],
                site_id=scope.site_id,
                revision_id=UUID(sealed["id"]),
                egress=None,
            )
