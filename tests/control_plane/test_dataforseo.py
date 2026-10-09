import asyncio
import base64
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_api.dataforseo_http import CapCommand, CredentialCommand, RemoveCommand
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_http import EgressHttpResult
from signal_core.dataforseo import DataForSeoQuery
from signal_core.dataforseo_credentials import DataForSeoUnavailable, OpenBaoDataForSeoCredentials
from signal_core.dataforseo_service import DataForSeoSettingsService, execute_call
from signal_core.egress_profiles import DataForSeoCredentialScope
from signal_core.keyword_topics import build_topics
from signal_core.seo_strategy_service import strategy_call
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import SharedEgressProvider
from test_bing_binding import owner_and_origin
from test_shared_egress import Fetcher, authority
from test_shared_egress import request as gateway_request
from test_shared_egress import response as gateway_response

LOGIN = "synthetic-login@example.invalid"
PASSWORD = "synthetic-dataforseo-password"
AUTH = "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()


def settings(api, context, site):
    secrets = {}

    def handler(request):
        path = request.url.path.replace("/metadata/", "/data/")
        if request.method == "DELETE":
            secrets.pop(path, None)
            return httpx2.Response(204)
        if request.method == "POST":
            if path in secrets:
                return httpx2.Response(400)
            secrets[path] = json.loads(request.content)["data"]
            return httpx2.Response(200, json={"data": {"version": 1}})
        if path not in secrets:
            return httpx2.Response(404)
        return httpx2.Response(
            200,
            json={
                "data": {
                    "data": secrets[path],
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )

    store = OpenBaoDataForSeoCredentials("https://bao.example.invalid", "synthetic-bao-token-0076")
    service = DataForSeoSettingsService(
        api, store, context["generation"], {"transport": httpx2.MockTransport(handler)}, True
    )
    return service, secrets


def configure(api, context, site, action, generation=None, cap=5000000):
    return api.execute(
        "SELECT * FROM control.configure_dataforseo(%s,%s,%s,%s,%s,%s,%s)",
        (
            hash_session_token(context["session_token"]),
            context["generation"],
            site,
            action,
            generation,
            cap,
            uuid4(),
        ),
    ).fetchone()


def reserve(connection, scope, generation, query, operation=None, estimate=90000):
    return connection.execute(
        "SELECT control.reserve_dataforseo(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            scope.tenant_id,
            scope.site_id,
            operation or uuid4(),
            generation,
            query.kind,
            query.endpoint,
            hashlib.sha256(query.body()).digest(),
            Jsonb(asdict(query)),
            estimate,
        ),
    ).fetchone()[0]


def enabled(admin, api, identity, context, scope):
    owner_and_origin(admin, identity, context, scope.site_id)
    generation = uuid4()
    assert configure(api, context, scope.site_id, "prepare", generation)[2] == "configured"
    assert configure(api, context, scope.site_id, "activate", generation)[2] == "configured"
    return generation


def test_owner_settings_openbao_only_unconfigured_caps_remove_rotation_and_denials(
    admin, api, identity, scopes, identity_context
):
    scope = scopes[0]
    service, secrets = settings(api, identity_context, scope.site_id)
    token = identity_context["session_token"]
    with pytest.raises(DataForSeoUnavailable, match="ACCESS_DENIED"):
        asyncio.run(service.read(token, scope.site_id))
    owner_and_origin(admin, identity, identity_context, scope.site_id)
    state = asyncio.run(service.read(token, scope.site_id))
    assert state["availability"] == "unconfigured" and state["cap_micros"] == 5000000
    assert set(state["features"].values()) == {"unavailable"}
    credential = CredentialCommand(operation="credential", login=LOGIN, password=PASSWORD)
    for _ in range(2):
        state = asyncio.run(service.control(token, scope.site_id, credential))
        assert state["availability"] == "available" and len(secrets) == 1
    assert "synthetic-login" not in json.dumps(state) and PASSWORD not in json.dumps(state)
    rows = admin.execute(
        "SELECT row_to_json(s)::text FROM app.dataforseo_settings s WHERE site_id=%s "
        "UNION ALL SELECT row_to_json(e)::text FROM app.dataforseo_settings_events e "
        "WHERE site_id=%s",
        (scope.site_id, scope.site_id),
    ).fetchall()
    assert all(LOGIN not in row[0] and PASSWORD not in row[0] for row in rows)
    state = asyncio.run(
        service.control(token, scope.site_id, CapCommand(operation="cap", cap_micros=0))
    )
    assert state["availability"] == "cap_exhausted"
    state = asyncio.run(service.control(token, scope.site_id, RemoveCommand(operation="remove")))
    assert state["availability"] == "unconfigured" and not secrets
    for other in scopes[1:]:
        with pytest.raises(DataForSeoUnavailable, match="ACCESS_DENIED"):
            asyncio.run(service.control(token, other.site_id, credential))
    assert not secrets
    stale = replace(service, recovery_generation="stale-generation")
    with pytest.raises(DataForSeoUnavailable):
        asyncio.run(stale.read(token, scope.site_id))
    for table in ("settings", "settings_events", "calls", "receipts"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(f"SELECT * FROM app.dataforseo_{table}")


def test_reservation_budget_idempotency_cap_exhaustion_concurrency_and_role_denial(
    admin, api, identity, crawl_ingest, scopes, identity_context
):
    scope = scopes[0]
    generation = enabled(admin, api, identity, identity_context, scope)
    query = DataForSeoQuery("volume", "widgets", 2840, "en")
    configure(api, identity_context, scope.site_id, "cap", cap=90000)

    def worker(_):
        with psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True
        ) as connection:
            return reserve(connection, scope, generation, query)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, range(2)))
    assert sorted(outcomes) == ["cap_exhausted", "reserved"]
    operation = admin.execute(
        "SELECT id FROM app.dataforseo_calls WHERE site_id=%s", (scope.site_id,)
    ).fetchone()[0]
    assert reserve(crawl_ingest, scope, generation, query, operation) == "replay"
    assert (
        reserve(
            crawl_ingest,
            scope,
            generation,
            DataForSeoQuery("volume", "different", 2840, "en"),
            operation,
        )
        == "conflict"
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reserve(api, scope, generation, query)
    assert reserve(crawl_ingest, scopes[1], generation, query) == "site_unavailable"
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        reserve(crawl_ingest, scope, generation, query, estimate=1)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        crawl_ingest.execute("DELETE FROM app.dataforseo_calls")


class Credentials:
    async def scope(self, tenant, site, generation):
        return DataForSeoCredentialScope(str(generation), AUTH)


@pytest.mark.parametrize(
    "kind,mode",
    [
        ("serp", "success"),
        ("volume", "success"),
        ("backlinks", "success"),
        ("volume", "malformed"),
        ("volume", "oversize"),
        ("volume", "echo"),
        ("volume", "auth"),
        ("volume", "overrun"),
        ("volume", "timeout"),
    ],
)
def test_gateway_paid_call_immutable_evidence_replay_and_failure(
    admin,
    api,
    identity,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
    kind,
    mode,
):
    scope = scopes[0]
    generation = enabled(admin, api, identity, identity_context, scope)
    query = DataForSeoQuery(
        kind,
        "competitor.example" if kind == "backlinks" else "widgets",
        None if kind == "backlinks" else 2840,
        None if kind == "backlinks" else "en",
    )
    if kind == "serp":
        result = {
            "keyword": query.subject,
            "location_code": 2840,
            "language_code": "en",
            "items": [
                {
                    "type": "organic",
                    "rank_group": 1,
                    "domain": "competitor.example",
                    "url": "https://competitor.example/",
                }
            ],
            "items_count": 1,
        }
    elif kind == "volume":
        result = {
            "keyword": query.subject,
            "location_code": 2840,
            "language_code": "en",
            "search_volume": 100,
        }
    else:
        result = {"target": query.subject, "backlinks": 100, "referring_domains": 20, "rank": 200}
    cost = {"serp": 0.002, "volume": 0.09, "backlinks": 0.024036}[kind]
    if mode == "overrun":
        cost = 0.3
    document = {
        "status_code": 20000,
        "cost": cost,
        "tasks_count": 1,
        "tasks_error": 0,
        "tasks": [
            {
                "id": "synthetic-task",
                "status_code": 20000,
                "cost": cost,
                "path": query.endpoint.split(".com/")[1].split("/"),
                "data": json.loads(query.body())[0],
                "result_count": 1,
                "result": [result],
            }
        ],
    }
    if mode == "malformed":
        result["search_volume"] = -1
    if mode == "echo":
        document["message"] = PASSWORD
    body = b"x" * 131073 if mode == "oversize" else json.dumps(document).encode()
    store = EncryptedLocalArtifactStore(tmp_path / "provider")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "dataforseo",
        store,
        origin_override="https://api.dataforseo.com",
    )
    response = EgressHttpResult(
        schema_version=1,
        request_url=query.endpoint,
        final_url=query.endpoint,
        method="POST",
        outcome="fetched",
        http_status=401 if mode == "auth" else 200,
        media_type="application/json",
        response_headers=(("content-type", "application/json"),),
        resolved_address=gateway_response(
            gateway_request("https://fixture.example.invalid")
        ).resolved_address,
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        decoded_bytes=len(body),
        elapsed_ms=12,
    )
    fetcher = Fetcher(
        response, error=TimeoutError("synthetic-secret-error") if mode == "timeout" else None
    )
    egress = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        fetcher,
        "worker.dataforseo",
        OriginAdmissionPolicy(),
        None,
        "connector",
    )
    operation = uuid4()
    args = dict(
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        credential_generation=generation,
        operation_id=operation,
        query=query,
        estimate_micros=180000,
    )
    # The official origin shares one global politeness bucket across all test sites.
    time.sleep(1.1)
    first = asyncio.run(execute_call(crawl_ingest, Credentials(), egress, **args))
    second = asyncio.run(execute_call(crawl_ingest, Credentials(), egress, **args))
    assert first == second
    assert first["status"] == (
        "complete"
        if mode in {"success", "overrun"}
        else "rejected"
        if mode in {"malformed", "echo"}
        else "unavailable"
        if mode == "auth"
        else "unknown"
    )
    assert len(fetcher.calls) == 1
    assert LOGIN not in json.dumps(first) and PASSWORD not in json.dumps(first)
    state = admin.execute(
        "SELECT egress_profile FROM app.egress_operations WHERE id=%s", (operation,)
    ).fetchone()
    assert state == ("dataforseo",)
    if mode in {"success", "overrun"}:
        assert first["result"]["kind"] == kind and first["reported_cost_micros"] == round(
            cost * 1000000
        )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute("DELETE FROM app.dataforseo_receipts WHERE call_id=%s", (operation,))
    assert admin.execute(
        "SELECT count(*) FROM app.dataforseo_calls WHERE id=%s", (operation,)
    ).fetchone() == (1,)
    usage = api.execute(
        "SELECT control.read_dataforseo(%s,%s,%s)",
        (
            hash_session_token(identity_context["session_token"]),
            identity_context["generation"],
            scope.site_id,
        ),
    ).fetchone()[0]["usage_micros"]
    assert usage == (round(cost * 1000000) if mode in {"success", "overrun"} else 180000)
    if kind == "volume":
        strategy_args = {
            "session_token": identity_context["session_token"],
            "generation": identity_context["generation"],
            "site_id": scope.site_id,
        }
        sources = strategy_call(api, **strategy_args, action="sources")
        records = sources["dataforseo"]["records"]
        assert len(records) == (1 if mode in {"success", "overrun"} else 0)
        if records:
            assert records[0]["id"] == str(operation)
            sources["gsc"]["records"] = [
                {
                    "id": str(uuid4()),
                    "dimensions": ["query"],
                    "coverage": {"complete": False},
                    "rows": [
                        {"keys": ["widgets"], "clicks": 1, "impressions": 200, "position": 12}
                    ],
                }
            ]
            topic = build_topics(sources)["clusters"][0]
            assert topic["metrics"]["impressions"] == 200
            assert topic["members"][0]["volumes"][0]["value"] == 100
            assert topic["members"][0]["volumes"][0]["evidence_ids"] == [str(operation)]
            if mode == "success":
                configure(api, identity_context, scope.site_id, "remove")
                assert not strategy_call(api, **strategy_args, action="sources")["dataforseo"][
                    "records"
                ]
    if mode == "overrun":
        configure(api, identity_context, scope.site_id, "cap", cap=180000)
        assert reserve(crawl_ingest, scope, generation, query) == "cap_exhausted"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        crawl_ingest.execute("UPDATE app.dataforseo_receipts SET status='complete'")


class NoIoEgress(SharedEgressProvider):
    def __init__(self, scope):
        object.__setattr__(self, "purpose", "connector")
        object.__setattr__(
            self, "run", SimpleNamespace(tenant_id=scope.tenant_id, site_id=scope.site_id)
        )

    def request_json(self, **kwargs):
        raise AssertionError("Unavailable research must not perform I/O.")


def test_unconfigured_and_exhausted_do_not_read_secrets_or_dispatch(
    admin, api, identity, crawl_ingest, scopes, identity_context
):
    scope = scopes[0]
    owner_and_origin(admin, identity, identity_context, scope.site_id)
    args = dict(
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        credential_generation=uuid4(),
        operation_id=uuid4(),
        query=DataForSeoQuery("volume", "widgets", 2840, "en"),
    )
    unconfigured = asyncio.run(execute_call(crawl_ingest, None, NoIoEgress(scope), **args))
    assert unconfigured["failure_code"] == "DATAFORSEO_UNCONFIGURED"
    configure(api, identity_context, scope.site_id, "prepare", args["credential_generation"])
    configure(api, identity_context, scope.site_id, "activate", args["credential_generation"])
    configure(api, identity_context, scope.site_id, "cap", cap=0)
    exhausted = asyncio.run(execute_call(crawl_ingest, None, NoIoEgress(scope), **args))
    assert exhausted["failure_code"] == "DATAFORSEO_CAP_EXHAUSTED"
    assert admin.execute(
        "SELECT count(*) FROM app.dataforseo_calls WHERE site_id=%s", (scope.site_id,)
    ).fetchone() == (0,)


@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
def test_current_owner_only_credential_and_cap_settings(
    admin, api, identity, scopes, identity_context, role
):
    scope = scopes[0]
    owner_and_origin(admin, identity, identity_context, scope.site_id)
    admin.execute(
        "UPDATE app.memberships SET role_key=%s WHERE id=%s",
        (role, identity_context["membership_id"]),
    )
    assert configure(api, identity_context, scope.site_id, "prepare", uuid4())[2] == "denied"
    assert configure(api, identity_context, scope.site_id, "cap", cap=1000000)[2] == "denied"
