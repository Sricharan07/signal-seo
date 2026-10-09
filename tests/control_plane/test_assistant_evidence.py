import asyncio
import hashlib
import json
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.assistant_providers import record_assistant_evidence, request_assistant_search
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_http import EgressHttpResult
from signal_core.shared_egress import SharedEgressProvider
from test_bing_binding import owner_and_origin
from test_shared_egress import Fetcher, authority


class Credentials:
    async def api_key(self, provider):
        return "synthetic-openai-key-00000000"


def test_assistant_evidence_requires_verified_site_and_matching_egress(
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
):
    scope = scopes[0]
    document = {"id": "resp_123", "model": "gpt-4.1-mini", "status": "completed", "output": []}
    body = json.dumps(document).encode()
    statement = (
        "SELECT control.record_assistant_provider_evidence("
        "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    args = (
        scope.tenant_id,
        scope.site_id,
        uuid4(),
        "openai",
        "gpt-4.1-mini",
        "gpt-4.1-mini",
        "resp_123",
        hashlib.sha256(b"request").digest(),
        hashlib.sha256(body).digest(),
        Jsonb(document),
        uuid4(),
    )
    assert crawl_ingest.execute(statement, args).fetchone() == ("origin_unavailable",)
    owner_and_origin(admin, identity, identity_context, scope.site_id)
    assert crawl_ingest.execute(statement, args).fetchone() == ("egress_evidence_unavailable",)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        crawl_ingest.execute(
            statement, (*args[:9], Jsonb({**document, "status": "queued"}), args[10])
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT * FROM app.assistant_provider_evidence")

    store = EncryptedLocalArtifactStore(tmp_path / "assistant")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "assistant",
        store,
        origin_override="https://api.openai.com",
    )
    outbound = EgressHttpResult(
        schema_version=1,
        request_url="https://api.openai.com/v1/responses",
        final_url="https://api.openai.com/v1/responses",
        method="POST",
        outcome="fetched",
        http_status=200,
        media_type="application/json",
        response_headers=(("content-type", "application/json"),),
        resolved_address="8.8.8.8",
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        decoded_bytes=len(body),
        elapsed_ms=12,
    )
    provider = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        Fetcher(outbound),
        "worker.assistant-egress",
        OriginAdmissionPolicy(),
        None,
    )
    result = asyncio.run(
        request_assistant_search(
            Credentials(),
            provider,
            provider="openai",
            question="Who cites this product site?",
            operation_id=uuid4(),
        )
    )
    assert admin.execute(
        "SELECT purpose, egress_profile FROM app.egress_operations WHERE id = %s",
        (result.egress_operation_id,),
    ).fetchone() == ("model", "openai_assistant")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        crawl_admission.execute(
            "SELECT control.bind_shared_egress_profile(%s, %s, %s, %s, %s)",
            (
                scope.tenant_id,
                scope.site_id,
                result.egress_operation_id,
                hashlib.sha256(b"wrong-request").digest(),
                "openai_assistant",
            ),
        )
    recorded = record_assistant_evidence(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        result=result,
    )
    stored = admin.execute(
        "SELECT provider, model_reported, response_id, response, response_sha256 "
        "FROM app.assistant_provider_evidence WHERE tenant_id = %s AND site_id = %s AND id = %s",
        (scope.tenant_id, scope.site_id, recorded.evidence_id),
    ).fetchone()
    assert stored == ("openai", "gpt-4.1-mini", "resp_123", document, hashlib.sha256(body).digest())
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        crawl_ingest.execute("DELETE FROM app.assistant_provider_evidence")
