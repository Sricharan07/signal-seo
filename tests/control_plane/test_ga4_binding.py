import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import date
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx2
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.ga4_binding import Ga4Service
from signal_core.ga4_protocol import GA4_SCOPE, Ga4Error
from signal_core.ga4_secrets import OpenBaoGa4Secrets
from signal_core.gsc_secrets import GscSecretError
from signal_core.shared_egress import SharedEgressProvider

from tests.connectors.test_ga4_protocol import ACCESS, CLIENT, report
from tests.control_plane.test_gsc_binding import owner_and_verified_origin, site_origin
from tests.control_plane.test_shared_egress import authority, response

REFRESH = "synthetic-ga4-refresh-secret"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Bao:
    def __init__(self):
        self.documents = {
            "oauth-client": (
                {"client_id": CLIENT.client_id, "client_secret": CLIENT.client_secret},
                1,
            )
        }

    def request(self, request):
        key = request.url.path.split("/data/")[-1]
        if request.method == "DELETE":
            key = request.url.path.split("/metadata/")[-1]
            self.documents.pop(key, None)
            return httpx2.Response(204)
        if request.method == "POST":
            if getattr(self, "fail_write", False):
                return httpx2.Response(503)
            document = json.loads(request.content)
            previous = self.documents.get(key, ({}, 0))[1]
            if document["options"]["cas"] != previous:
                return httpx2.Response(400)
            self.documents[key] = document["data"], previous + 1
            return httpx2.Response(200, json={"data": {"version": previous + 1}})
        if key not in self.documents:
            return httpx2.Response(404)
        data, version = self.documents[key]
        return httpx2.Response(
            200,
            json={
                "data": {
                    "data": data,
                    "metadata": {"version": version, "destroyed": False, "deletion_time": ""},
                }
            },
        )


class Secrets(OpenBaoGa4Secrets):
    async def _request(self, method, path, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(self.bao.request)
        return await super()._request(method, path, **kwargs)


def service(identity, context):
    secrets = Secrets("https://bao.example.invalid", "synthetic-ga4-bao-token")
    object.__setattr__(secrets, "bao", Bao())
    return Ga4Service(
        identity,
        secrets,
        context["generation"],
        "https://dashboard.example.invalid/auth/ga4/callback",
    )


def mfa(admin, context):
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (context["identity_session_id"],),
    )
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE id=%s", (context["tenant_session_id"],)
    )


class Google:
    def __init__(self, origin):
        self.origin = origin
        self.scope = GA4_SCOPE
        self.refresh_token = REFRESH
        self.calls = []
        self.report_status = 200
        self.on_report = None

    def request(self, outbound, *, policy):
        self.calls.append(outbound)
        if outbound.url.endswith("/token"):
            doc = {
                "access_token": ACCESS,
                "refresh_token": self.refresh_token,
                "scope": self.scope,
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        elif "accountSummaries" in outbound.url:
            doc = {
                "accountSummaries": [
                    {
                        "propertySummaries": [
                            {"property": "properties/123", "displayName": "Synthetic GA4"}
                        ]
                    }
                ]
            }
        elif "dataStreams" in outbound.url:
            doc = {
                "dataStreams": [
                    {"type": "WEB_DATA_STREAM", "webStreamData": {"defaultUri": self.origin + "/"}}
                ]
            }
        elif outbound.url.endswith(":runReport"):
            if self.on_report:
                self.on_report()
            doc = report(["/"], metadata={"subjectToThresholding": True, "timeZone": "UTC"})
        else:
            assert outbound.url.endswith("/revoke")
            doc = {}
        body = json.dumps(doc).encode()
        # The existing result fixture supplies the test boundary's admitted peer.
        proxy = type("Request", (), {"http": outbound})()
        base = response(proxy)
        return replace(
            base,
            body=body,
            body_sha256=hashlib.sha256(body).hexdigest(),
            decoded_bytes=len(body),
            http_status=self.report_status if outbound.url.endswith(":runReport") else 200,
        )


class Router(SharedEgressProvider):
    def __init__(self, providers):
        object.__setattr__(self, "purpose", "connector")
        self.providers = providers

    def request_json(self, **kwargs):
        time.sleep(1.1)
        origin = "https://" + urlsplit(kwargs["url"]).netloc
        return self.providers[origin].request_json(**kwargs)


def egress(api, scheduler, workflow, admission, ingest, scope, tmp_path, google):
    providers = {}
    for origin in (
        "https://oauth2.googleapis.com",
        "https://analyticsadmin.googleapis.com",
        "https://analyticsdata.googleapis.com",
    ):
        store = EncryptedLocalArtifactStore(tmp_path / uuid4().hex)
        run, policy, _ = authority(
            api,
            scheduler,
            workflow,
            admission,
            ingest,
            scope,
            "ga4-" + uuid4().hex,
            store,
            origin_override=origin,
            github_profile=True,
        )
        providers[origin] = SharedEgressProvider(
            admission,
            ingest,
            store,
            run,
            policy,
            google,
            "synthetic-ga4-worker",
            OriginAdmissionPolicy(),
            None,
            "connector",
        )
    return Router(providers)


async def bound(svc, provider, context, site):
    token = context["session_token"]
    begun = await svc.begin(token, site)
    state = parse_qs(urlsplit(begun["authorization_url"]).query)["state"][0]
    selected = await svc.complete(
        token,
        site,
        attempt_id=begun["attempt_id"],
        state=state,
        code="synthetic-ga4-code",
        egress=provider,
    )
    return await svc.confirm(
        token,
        site,
        attempt_id=selected["attempt_id"],
        property_resource_name="properties/123",
        egress=provider,
    )


@pytest.mark.anyio
async def test_owner_mfa_exact_property_import_revoke_and_restore(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    svc = service(identity, identity_context)
    with pytest.raises(Ga4Error, match="GA4_OWNER_MFA_OR_ORIGIN_DENIED"):
        await svc.begin(identity_context["session_token"], scope.site_id)
    mfa(admin, identity_context)
    google = Google(site_origin(scope.site_id))
    provider = egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path, google
    )
    binding = await bound(svc, provider, identity_context, scope.site_id)
    result = await svc.import_report(
        identity_context["session_token"],
        scope.site_id,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 28),
        egress=provider,
    )
    assert result["coverage"]["thresholding"] and not result["coverage"]["complete"]
    assert svc.read(identity_context["session_token"], scope.site_id)["generation_id"] == str(
        result["generation_id"]
    )
    for table in (
        "ga4_oauth_attempts",
        "ga4_bindings",
        "ga4_import_generations",
        "egress_operations",
    ):
        rows = admin.execute(
            f"SELECT to_jsonb(t)::text FROM app.{table} t WHERE tenant_id=%s", (scope.tenant_id,)
        ).fetchall()
        assert all(
            ACCESS not in r[0] and REFRESH not in r[0] and CLIENT.client_secret not in r[0]
            for r in rows
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            identity.execute(f"DELETE FROM app.{table}")
    result = await svc.disconnect(
        identity_context["session_token"],
        scope.site_id,
        binding_id=binding["binding_id"],
        egress=provider,
    )
    assert result["state"] == "AUTHORITY_DURABILITY_PENDING"
    assert result["upstream_revoked"]
    assert not any(k.startswith("refresh/") for k in svc.secrets_store.bao.documents)
    assert svc.read(identity_context["session_token"], scope.site_id)["state"] == "disconnected"
    event = admin.execute(
        "SELECT event_id FROM control.authority_restriction_outbox WHERE target_id=%s",
        (binding["binding_id"],),
    ).fetchone()[0]
    with psycopg.connect(
        os.environ["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"], autocommit=True
    ) as dispatcher:
        stream = uuid4()
        args = (event, binding["binding_id"], 1, stream, 1, "a" * 64)
        dispatcher.execute("SELECT control.apply_ga4_binding_denial(%s,%s,%s,%s,%s,%s)", args)
        dispatcher.execute("SELECT control.apply_ga4_binding_denial(%s,%s,%s,%s,%s,%s)", args)
        with pytest.raises(psycopg.errors.UniqueViolation):
            dispatcher.execute(
                "SELECT control.apply_ga4_binding_denial(%s,%s,%s,%s,%s,%s)", (*args[:-1], "b" * 64)
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute("SELECT control.apply_ga4_binding_denial(%s,%s,%s,%s,%s,%s)", args)


@pytest.mark.anyio
async def test_state_redirect_pkce_role_and_tenant_negatives(
    admin, identity, scopes, identity_context
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    mfa(admin, identity_context)
    svc = service(identity, identity_context)
    begun = await svc.begin(identity_context["session_token"], scope.site_id)
    attempt = begun["attempt_id"]
    state = parse_qs(urlsplit(begun["authorization_url"]).query)["state"][0]
    for changes in ({"state": "synthetic-" + "w" * 33}, {"site": scopes[1].site_id}):
        with pytest.raises(Ga4Error, match="GA4_ATTEMPT_UNAVAILABLE"):
            await svc.complete(
                identity_context["session_token"],
                changes.get("site", scope.site_id),
                attempt_id=attempt,
                state=changes.get("state", state),
                code="synthetic-ga4-code",
                egress=None,
            )
    svc.redirect_uri += "/wrong"
    with pytest.raises(Ga4Error, match="GA4_ATTEMPT_UNAVAILABLE"):
        await svc.complete(
            identity_context["session_token"],
            scope.site_id,
            attempt_id=attempt,
            state=state,
            code="synthetic-ga4-code",
            egress=None,
        )
    svc.redirect_uri = svc.redirect_uri.removesuffix("/wrong")
    svc.secrets_store.bao.documents[f"verifiers/{attempt}"] = (
        {"code_verifier": "synthetic-" + "v" * 43},
        1,
    )
    with pytest.raises(Ga4Error, match="GA4_PKCE_REJECTED"):
        await svc.complete(
            identity_context["session_token"],
            scope.site_id,
            attempt_id=attempt,
            state=state,
            code="synthetic-ga4-code",
            egress=None,
        )
    with pytest.raises(Ga4Error, match="GA4_ATTEMPT_UNAVAILABLE"):
        await svc.complete(
            identity_context["session_token"],
            scope.site_id,
            attempt_id=attempt,
            state=state,
            code="synthetic-ga4-code",
            egress=None,
        )
    for role in ("analyst", "viewer", "admin"):
        admin.execute(
            "UPDATE app.memberships SET role_key=%s WHERE id=%s",
            (role, identity_context["membership_id"]),
        )
        with pytest.raises(Ga4Error):
            svc.read(identity_context["session_token"], scope.site_id)
    with pytest.raises(GscSecretError):
        await svc.secrets_store.refresh_token(f"secret://gsc/{uuid4()}")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", ["origin", "scope", "rotation", "reauth", "recovery", "revoked"]
)
async def test_provider_and_recovery_fail_closed(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
    failure,
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    mfa(admin, identity_context)
    svc = service(identity, identity_context)
    google = Google(site_origin(scope.site_id))
    provider = egress(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path, google
    )
    if failure == "origin":
        google.origin = "https://other.example.invalid"
        with pytest.raises(Ga4Error, match="GA4_PROPERTY_ORIGIN_MISMATCH"):
            await bound(svc, provider, identity_context, scope.site_id)
        assert admin.execute(
            "SELECT count(*) FROM app.ga4_bindings WHERE tenant_id=%s AND site_id=%s",
            (scope.tenant_id, scope.site_id),
        ).fetchone() == (0,)
        return
    binding = await bound(svc, provider, identity_context, scope.site_id)
    generation = identity_context["generation"]

    async def current():
        return generation

    svc.current_generation = current
    if failure == "scope":
        google.scope = "https://www.googleapis.com/auth/analytics.edit"
        expected = "GSC_REDUCED_SCOPE"
    elif failure == "rotation":
        google.refresh_token += "-rotated"
        svc.secrets_store.bao.fail_write = True
        expected = "GSC_SECRET_WRITE_FAILED"
    elif failure == "reauth":
        google.report_status = 403
        expected = "GA4_REAUTH_REQUIRED"
    elif failure == "recovery":

        def recover():
            nonlocal generation
            generation = "synthetic-recovered-generation"

        google.on_report = recover
        expected = "GA4_RECOVERY_CHANGED"
    else:

        def revoke():
            assert (
                identity.execute(
                    "SELECT * FROM control.revoke_ga4_binding(%s,%s,%s,%s,%s)",
                    (
                        *svc._args(identity_context["session_token"], scope.site_id),
                        binding["binding_id"],
                        uuid4(),
                    ),
                ).fetchone()[0]
                == "AUTHORITY_DURABILITY_PENDING"
            )

        google.on_report = revoke
        expected = "GA4_IMPORT_REJECTED"
    with pytest.raises(Exception, match=expected) as error:
        await svc.import_report(
            identity_context["session_token"],
            scope.site_id,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 28),
            egress=provider,
        )
    assert ACCESS not in str(error.value) and REFRESH not in str(error.value)
    assert admin.execute(
        "SELECT count(*) FROM app.ga4_import_generations WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (0,)
    if failure in {"scope", "rotation", "reauth", "revoked"}:
        assert svc.read(identity_context["session_token"], scope.site_id)["state"] == "disconnected"


def test_failed_ga4_migration_rolls_back_to_main_head(admin):
    name = "synthetic_ga4_migration_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        command = [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade"]
        prior = subprocess.run(
            command + ["0060"], env=env, capture_output=True, text=True, timeout=60
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE app.ga4_import_generations (id integer)")
        failed = subprocess.run(
            command + ["head"], env=env, capture_output=True, text=True, timeout=60
        )
        assert failed.returncode != 0
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchall() == [("0060",)]
            assert connection.execute(
                "SELECT to_regclass('app.ga4_bindings'), "
                "to_regclass('control.ga4_binding_restrictions')"
            ).fetchone() == (None, None)
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
