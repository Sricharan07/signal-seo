import hashlib
import os
import time
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_http import CrawlFetchUnavailable, EgressHttpResult
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.egress_profiles import EgressProfile
from signal_core.owner_connector_egress import OwnerConnectorContext
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider

from tests.control_plane.test_gsc_binding import owner_and_verified_origin


class Fetcher:
    def __init__(self, robots=b"User-agent: *\nAllow: /\n", status=200):
        self.robots, self.status, self.calls = robots, status, []
        self.fail = False

    def request(self, request, *, policy):
        self.calls.append(request)
        if self.fail:
            raise CrawlFetchUnavailable()
        robots = request.url.endswith("/robots.txt")
        body = self.robots if robots else b'{"ok":true}'
        return EgressHttpResult(
            schema_version=1,
            method=request.method,
            request_url=request.url,
            final_url=request.url,
            outcome="fetched",
            http_status=200 if robots else self.status,
            media_type="text/plain" if robots else "application/json",
            response_headers=(),
            resolved_address="93.184.216.34",
            body=body,
            body_sha256=hashlib.sha256(body).hexdigest(),
            decoded_bytes=len(body),
            elapsed_ms=5,
        )


@pytest.fixture
def provider(admin, identity, identity_context, scopes, tmp_path):
    site_id = scopes[0].site_id
    owner_and_verified_origin(admin, identity, identity_context, site_id)
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (identity_context["identity_session_id"],),
    )
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE id=%s",
        (identity_context["tenant_session_id"],),
    )
    with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as evidence:
        yield SharedEgressProvider(
            identity,
            evidence,
            EncryptedLocalArtifactStore(tmp_path / "artifacts"),
            OwnerConnectorContext(
                scopes[0].tenant_id,
                site_id,
                identity_context["session_token"],
                identity_context["generation"],
            ),
            CrawlScopePolicy(
                1,
                ("https://slack.com",),
                "SignalBot/1.0 (+https://signal.example/bot)",
                max_redirects=0,
                max_body_bytes=16384,
                request_timeout_seconds=5,
                total_timeout_seconds=5,
            ),
            Fetcher(),
            "worker.owner-connectors",
            OriginAdmissionPolicy(),
            ArtifactEncryptionKey("artifact-key:v1:owner-connectors-test", b"K" * 32),
            purpose="connector",
        )


def send(provider, operation_id=None):
    return provider.request_json(
        method="POST",
        url="https://slack.com/api/auth.revoke",
        profile=EgressProfile.SLACK_BOT,
        authorization="Bearer synthetic-disposable-token",
        body=b"{}",
        operation_id=operation_id or uuid4(),
        timeout_seconds=5,
        max_response_bytes=16384,
    )


def settled(provider, operation_id):
    deadline = time.monotonic() + 3
    while True:
        try:
            return send(provider, operation_id)
        except ProviderEgressUnavailable as error:
            if error.code != "EGRESS_DEFERRED" or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


@pytest.mark.parametrize("revoked", [False, True])
def test_composed_owner_factory_uses_real_authority_and_closes_private_connections(
    provider,
    admin,
    monkeypatch,
    revoked,
):
    from psycopg import sql
    from signal_api.integration_connectors import OwnerEgressFactory

    connections = []

    def connect():
        connection = psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True)
        connections.append(connection)
        return connection

    from signal_core.integration_scope import load_integration_scope

    integration_scope = replace(load_integration_scope(), site_id=str(provider.run.site_id))
    monkeypatch.setattr(
        "signal_api.integration_connectors.PinnedHttpFetcher", lambda resolver: provider.fetcher
    )
    factory = OwnerEgressFactory(
        connect,
        provider.store,
        provider.artifact_key,
        None,
        "slack",
        integration_scope=integration_scope,
    )
    if revoked:
        admin.execute(
            "UPDATE app.sessions SET revoked_at=clock_timestamp() WHERE session_token_hash=%s",
            (hashlib.sha256(provider.run.session_token.encode()).digest(),),
        )
        with (
            pytest.raises(ValueError),
            factory(
                provider.run.session_token,
                provider.run.site_id,
                provider.run.recovery_generation,
            ),
        ):
            pass
        assert not provider.fetcher.calls
    else:
        # Existing shared-origin backoff from another fixture is respected.
        table = sql.SQL("control.origin_buckets")
        deadline = time.monotonic() + 8
        while admin.execute(
            sql.SQL(
                "SELECT 1 FROM {} WHERE origin='https://slack.com' "
                "AND next_allowed_at>clock_timestamp()"
            ).format(table)
        ).fetchone():
            assert time.monotonic() < deadline
            time.sleep(0.1)
        with factory(
            provider.run.session_token, provider.run.site_id, provider.run.recovery_generation
        ) as composed:
            assert send(composed).status_code == 200
            assert composed.run.tenant_id == provider.run.tenant_id
        assert len(provider.fetcher.calls) == 2
    assert len(connections) == 2 and all(connection.closed for connection in connections)


def test_real_owner_dispatch_records_robots_and_provider_without_a_crawl(provider, admin):
    operation = uuid4()
    result = settled(provider, operation)
    assert result.status_code == 200
    assert len(provider.fetcher.calls) == 2
    rows = admin.execute(
        "SELECT kind,state,response_evidence,request_sha256 FROM "
        "app.owner_connector_egress_operations WHERE site_id=%s ORDER BY issued_at",
        (provider.run.site_id,),
    ).fetchall()
    assert [row[:2] for row in rows] == [("robots", "observed"), ("provider", "observed")]
    assert (
        rows[0][2]["robots_artifact"]["encryption_key_ref"]
        == "artifact-key:v1:owner-connectors-test"
    )
    assert "synthetic-disposable-token" not in str(rows)
    assert admin.execute(
        "SELECT count(*) FROM app.crawl_runs WHERE site_id=%s", (provider.run.site_id,)
    ).fetchone() == (0,)
    assert admin.execute(
        "SELECT count(*) FROM app.standing_authorizations WHERE site_id=%s", (provider.run.site_id,)
    ).fetchone() == (0,)
    with pytest.raises(ProviderEgressUnavailable, match="EGRESS_BODY_NOT_RETAINED"):
        send(provider, operation)
    assert len(provider.fetcher.calls) == 2


@pytest.mark.parametrize("change", ["primary", "generation", "site", "tenant", "revoked"])
def test_current_owner_authority_is_required_before_any_network(provider, admin, change):
    if change == "primary":
        admin.execute(
            "UPDATE control.identity_sessions SET authentication_level='primary' WHERE "
            "user_id IN (SELECT actor_user_id FROM app.owner_connector_egress_operations) "
            "OR id IN (SELECT identity_session_id FROM app.sessions WHERE active_site_id=%s)",
            (provider.run.site_id,),
        )
    elif change == "revoked":
        admin.execute(
            "UPDATE app.sessions SET revoked_at=clock_timestamp() WHERE active_site_id=%s",
            (provider.run.site_id,),
        )
    else:
        field = {"generation": "recovery_generation", "site": "site_id", "tenant": "tenant_id"}[
            change
        ]
        value = "wrong-generation" if change == "generation" else uuid4()
        provider = replace(provider, run=replace(provider.run, **{field: value}))
    with pytest.raises(ProviderEgressUnavailable, match="EGRESS_AUTHORITY_UNAVAILABLE"):
        send(provider)
    assert provider.fetcher.calls == []


def test_robots_denial_and_transport_failure_never_send_credentials(provider, admin):
    provider.fetcher.robots = b"User-agent: *\nDisallow: /api/\n"
    with pytest.raises(ProviderEgressUnavailable, match="EGRESS_ROBOTS_DENIED"):
        settled(provider, uuid4())
    assert len(provider.fetcher.calls) == 1
    assert "authorization" not in dict(provider.fetcher.calls[0].headers)
    provider.fetcher.fail = True
    with pytest.raises(ProviderEgressUnavailable, match="EGRESS_ROBOTS_DENIED"):
        settled(provider, uuid4())
    assert admin.execute(
        "SELECT state FROM app.owner_connector_egress_operations WHERE site_id=%s "
        "ORDER BY issued_at DESC LIMIT 1",
        (provider.run.site_id,),
    ).fetchone() == ("failed",)


def test_model_and_repository_write_profiles_cannot_borrow_owner_authority(provider):
    with pytest.raises(ValueError):
        provider.request_json(
            method="POST",
            url="https://api.openai.com/v1/responses",
            profile=EgressProfile.OPENAI_MODEL,
            authorization="Bearer test-only",
            body=b"{}",
            operation_id=uuid4(),
            timeout_seconds=5,
            max_response_bytes=4096,
        )
    assert provider.fetcher.calls == []


def test_runtime_role_cannot_read_or_mutate_owner_operation_table(provider):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        provider.admission_connection.execute("SELECT * FROM app.owner_connector_egress_operations")


def begin_robots(provider, operation, *, target="https://slack.com/api/auth.revoke"):
    return provider.admission_connection.execute(
        "SELECT * FROM control.begin_owner_connector_egress(" + ",".join(["%s"] * 17) + ")",
        (
            hashlib.sha256(provider.run.session_token.encode()).digest(),
            provider.run.recovery_generation,
            provider.run.site_id,
            operation,
            "robots",
            "slack_bot",
            "GET",
            "https://slack.com/robots.txt",
            target,
            "https://slack.com",
            b"R" * 32,
            None,
            0,
            16384,
            False,
            None,
            1000,
        ),
    ).fetchone()


def admit_robots(provider, operation):
    deadline = time.monotonic() + 8
    while True:
        row = begin_robots(provider, operation)
        if row[0] != "deferred" or time.monotonic() >= deadline:
            assert row[0] == "admitted"
            return row
        time.sleep(0.1)


def finish_robots(provider, operation, evidence, *, allowed=True, rules=None):
    return provider.ingest_connection.execute(
        "SELECT control.finish_owner_connector_egress(" + ",".join(["%s"] * 10) + ")",
        (
            hashlib.sha256(provider.run.session_token.encode()).digest(),
            provider.run.recovery_generation,
            provider.run.site_id,
            operation,
            Jsonb(evidence),
            "success",
            None,
            allowed,
            rules,
            None,
        ),
    ).fetchone()[0]


def missing_robots_evidence():
    return {
        "outcome": "fetched",
        "http_status": 404,
        "media_type": "text/plain",
        "resolved_address": "93.184.216.34",
        "body_sha256": hashlib.sha256(b"").hexdigest(),
        "decoded_bytes": 0,
        "elapsed_ms": 1,
        "robots_artifact": None,
    }


@pytest.mark.parametrize(
    "target",
    [
        "https://slack.com/api/admin.users.list",
        "https://slack.com/api/auth.revoke?token=forbidden",
        "http://slack.com/api/auth.revoke",
        "https://169.254.169.254/latest/meta-data/",
    ],
)
def test_sql_closed_profiles_reject_unlisted_paths_and_origins(provider, target):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        begin_robots(provider, uuid4(), target=target)
    assert provider.fetcher.calls == []


@pytest.mark.parametrize(
    "invalid",
    ["missing_number", "null_number", "private_peer", "missing_rules", "missing_artifact"],
)
def test_malformed_or_unproved_robots_completion_leaves_dispatch_unknown(provider, invalid):
    operation = uuid4()
    admit_robots(provider, operation)
    evidence = missing_robots_evidence()
    rules = None
    if invalid == "missing_number":
        evidence.pop("elapsed_ms")
    elif invalid == "null_number":
        evidence["decoded_bytes"] = None
    elif invalid == "private_peer":
        evidence["resolved_address"] = "169.254.169.254"
    else:
        evidence["http_status"] = 200
        if invalid == "missing_artifact":
            rules = b"S" * 32
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        finish_robots(provider, operation, evidence, rules=rules)
    assert begin_robots(provider, operation)[0] == "unknown"
    assert begin_robots(provider, uuid4())[0] == "deferred"
    assert finish_robots(provider, operation, missing_robots_evidence()) == "finished"


def test_exact_missing_robots_replay_and_conflicting_completion(provider):
    operation = uuid4()
    admit_robots(provider, operation)
    evidence = missing_robots_evidence()
    assert finish_robots(provider, operation, evidence) == "finished"
    assert finish_robots(provider, operation, evidence) == "replayed"
    assert begin_robots(provider, operation)[0] == "robots_replayed"
    assert finish_robots(provider, operation, {**evidence, "elapsed_ms": 2}) == "conflict"
