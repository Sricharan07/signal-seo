import hashlib
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.bing_binding import (
    BingBindingError,
    import_bing_observation,
    read_bing_page_performance,
)
from signal_core.bing_protocol import BING_API_URL, BingProtocolError
from signal_core.bing_secrets import BingClientCredentials
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.shared_egress import SharedEgressProvider
from test_bing_binding import origin, owner_and_origin, session_args
from test_shared_egress import authority, response

TOKEN = "synthetic-bing-pages-access-token"
DATE = "/Date(1316156400000-0700)/"
RECORD = "SELECT control.record_bing_page_import_generation(" + ",".join(["%s"] * 10) + ")"


class SyntheticSecrets:
    async def refresh_token(self, reference):
        return "synthetic-bing-pages-refresh-token"

    async def client_credentials(self):
        return BingClientCredentials("synthetic-bing-client", "synthetic-bing-client-secret")


class BingFetcher:
    def __init__(self, site, *, status=200, revoked=None):
        self.calls = []
        self.status = status
        self.revoked = revoked
        self.document = {
            "d": [
                {
                    "Query": site + "page",
                    "Date": DATE,
                    "Clicks": 2147483648,
                    "Impressions": 9223372036854775807,
                    "AvgClickPosition": 2,
                    "AvgImpressionPosition": 4,
                },
                {
                    "Query": "https://other.example.invalid/page",
                    "Date": DATE,
                    "Clicks": 1,
                    "Impressions": 10,
                    "AvgClickPosition": 2,
                    "AvgImpressionPosition": 4,
                },
            ]
        }

    def request(self, request, *, policy):
        self.calls.append(request)
        token_call = request.url.endswith("/oauth/token")
        if token_call:
            document = {"access_token": TOKEN, "expires_in": 3600, "token_type": "Bearer"}
        else:
            document = self.document
            if self.revoked:
                self.revoked()
        body = json.dumps(document).encode()
        result = replace(
            response(SimpleNamespace(http=request), status=200 if token_call else self.status),
            body=body,
            body_sha256=hashlib.sha256(body).hexdigest(),
            decoded_bytes=len(body),
        )
        if token_call:
            time.sleep(1.05)
        return result


@pytest.fixture
def pages_context(
    admin,
    identity,
    identity_context,
    scopes,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    tmp_path,
):
    scope = scopes[0]
    site = origin(scope.site_id) + "/"
    owner_and_origin(admin, identity, identity_context, scope.site_id)
    attempt, binding = uuid4(), uuid4()
    common = (*session_args(identity_context, scope.site_id), attempt)
    digest = hashlib.sha256(attempt.bytes).digest()
    callback = "https://signal.example.invalid/bing/callback"
    assert identity.execute(
        "SELECT control.begin_bing_oauth_attempt(%s,%s,%s,%s,%s,%s)", (*common, digest, callback)
    ).fetchone() == ("created",)
    assert identity.execute(
        "SELECT outcome FROM control.consume_bing_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (*common, digest, callback),
    ).fetchone() == ("consumed",)
    assert identity.execute(
        "SELECT control.stage_bing_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (*common, f"secret://bing/{attempt}", Jsonb([{"url": site, "eligible": True}])),
    ).fetchone() == ("staged",)
    assert identity.execute(
        "SELECT control.confirm_bing_binding(%s,%s,%s,%s,%s,%s,%s)",
        (*common, binding, uuid4(), site),
    ).fetchone() == ("bound",)
    store = EncryptedLocalArtifactStore(tmp_path / "bing-pages")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "bing-pages",
        store,
        origin="https://www.bing.com",
    )
    fetcher = BingFetcher(site)
    provider = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        fetcher,
        "worker.bing-pages",
        OriginAdmissionPolicy(),
        None,
        "connector",
    )
    delay = admin.execute(
        "SELECT extract(epoch FROM (greatest(next_allowed_at, degraded_until) - "
        "statement_timestamp())) FROM control.origin_buckets WHERE origin=%s",
        ("https://www.bing.com",),
    ).fetchone()
    if delay is not None and delay[0] > 0:
        time.sleep(float(delay[0]) + 0.05)
    return scope, site, binding, fetcher, provider


async def import_pages(connection, context):
    scope, _, _, _, provider = context
    operation = uuid4()
    result = await import_bing_observation(
        connection,
        SyntheticSecrets(),
        provider,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        kind="page_performance",
        refresh_operation_id=uuid4(),
        import_operation_id=operation,
    )
    return result, operation


@pytest.mark.anyio
async def test_import_read_coverage_verbatim_isolation_and_revocation(
    admin, api, identity, crawl_ingest, scopes, identity_context, pages_context, caplog
):
    scope, site, binding, fetcher, _ = pages_context
    assert (
        read_bing_page_performance(crawl_ingest, tenant_id=scope.tenant_id, site_id=scope.site_id)
        is None
    )
    with pytest.raises(BingBindingError, match="BING_SCOPE_REJECTED"):
        await import_bing_observation(
            crawl_ingest,
            SyntheticSecrets(),
            pages_context[4],
            tenant_id=scopes[2].tenant_id,
            site_id=scope.site_id,
            kind="page_performance",
            refresh_operation_id=uuid4(),
            import_operation_id=uuid4(),
        )
    assert fetcher.calls == []
    result, operation = await import_pages(crawl_ingest, pages_context)
    assert result.observation.rows[0]["page_url"] == site + "page"
    assert result.observation.coverage["dropped_out_of_site_rows"] == 1
    assert (
        read_bing_page_performance(crawl_ingest, tenant_id=scope.tenant_id, site_id=scope.site_id)
        == result
    )
    stored = admin.execute(
        "SELECT rows,coverage FROM app.bing_import_generations WHERE id=%s", (result.generation_id,)
    ).fetchone()
    assert stored == (list(result.observation.rows), result.observation.coverage)
    for tenant, site_id in [
        (scopes[2].tenant_id, scope.site_id),
        (scope.tenant_id, scopes[1].site_id),
        (scopes[2].tenant_id, scopes[2].site_id),
    ]:
        assert read_bing_page_performance(crawl_ingest, tenant_id=tenant, site_id=site_id) is None
    for connection in (api, identity):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            read_bing_page_performance(connection, tenant_id=scope.tenant_id, site_id=scope.site_id)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM app.bing_import_generations")
    evidence = admin.execute(
        "SELECT to_jsonb(e)::text FROM app.egress_operations e WHERE id=%s", (operation,)
    ).fetchone()[0]
    assert TOKEN not in evidence + str(stored) + caplog.text
    assert admin.execute(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
        "WHERE oid='app.bing_import_generations'::regclass"
    ).fetchone() == (True, True)
    assert len(fetcher.calls) == 2
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "DELETE FROM app.bing_import_generations WHERE id=%s", (result.generation_id,)
        )
    assert identity.execute(
        "SELECT outcome FROM control.revoke_bing_binding(%s,%s,%s,%s,%s)",
        (*session_args(identity_context, scope.site_id), binding, uuid4()),
    ).fetchone() == ("revoked",)
    assert (
        read_bing_page_performance(crawl_ingest, tenant_id=scope.tenant_id, site_id=scope.site_id)
        is None
    )
    with pytest.raises(BingBindingError, match="BING_BINDING_UNAVAILABLE"):
        await import_pages(crawl_ingest, pages_context)
    assert len(fetcher.calls) == 2


@pytest.mark.anyio
async def test_sql_strict_validation_profile_digest_site_and_role_negatives(
    admin, api, identity, crawl_ingest, pages_context
):
    scope, site, binding, _, _ = pages_context
    result, operation = await import_pages(crawl_ingest, pages_context)
    base = [
        scope.tenant_id,
        scope.site_id,
        uuid4(),
        binding,
        site,
        "page_performance",
        Jsonb(list(result.observation.rows)),
        Jsonb(result.observation.coverage),
        result.observation.response_sha256,
        operation,
    ]
    row = result.observation.rows[0]
    for changes in (
        {"clicks": True},
        {"clicks": -1},
        {"clicks": 9223372036854775808},
        {"avg_impression_position": 2147483648},
        {"avg_impression_position": "1"},
        {"avg_click_position": None},
        {"date": "today"},
        {"date": "/Date(9999999999999999+0000)/"},
        {"page_url": "https://other.example.invalid/page"},
        {"page_url": site + "%ZZ"},
        {"page_url": site + "%0a"},
        {"page_url": site + "x#f"},
        {"secret": TOKEN},
    ):
        args = [*base]
        args[6] = Jsonb([{**row, **changes}])
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            crawl_ingest.execute(RECORD, args)
    for coverage in (
        {**result.observation.coverage, "complete": True},
        {**result.observation.coverage, "complete": "false"},
        {**result.observation.coverage, "missing_data": "zero"},
        {**result.observation.coverage, "date_granularity": "daily"},
    ):
        args = [*base]
        args[7] = Jsonb(coverage)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            crawl_ingest.execute(RECORD, args)
    args = [*base]
    args[6] = Jsonb([row, row])
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        crawl_ingest.execute(RECORD, args)
    for connection in (api, identity):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(RECORD, base)
    args = [*base]
    args[8] = b"x" * 32
    assert crawl_ingest.execute(RECORD, args).fetchone() == ("egress_evidence_unavailable",)
    refresh_evidence = admin.execute(
        "SELECT id,response_sha256 FROM app.egress_operations "
        "WHERE site_id=%s AND egress_profile='bing_oauth_token'",
        (scope.site_id,),
    ).fetchone()
    args = [*base]
    args[8], args[9] = refresh_evidence[1], refresh_evidence[0]
    assert crawl_ingest.execute(RECORD, args).fetchone() == ("egress_evidence_unavailable",)
    # Independent egress evidence cannot be borrowed for a different site/profile/path.
    expected_url = BING_API_URL + "/GetPageStats?" + urlencode({"siteUrl": site})
    assert admin.execute(
        "SELECT request_url FROM app.egress_operations WHERE id=%s", (operation,)
    ).fetchone() == (expected_url,)
    args = [*base]
    args[4] = "https://wrong.example.invalid/"
    assert crawl_ingest.execute(RECORD, args).fetchone() == ("binding_unavailable",)
    extra = {**result.observation.coverage, "future_coverage": {"known": False}}
    args = [*base]
    args[7] = Jsonb(extra)
    assert crawl_ingest.execute(RECORD, args).fetchone() == ("recorded",)
    assert admin.execute(
        "SELECT coverage FROM app.bing_import_generations WHERE id=%s", (args[2],)
    ).fetchone() == (extra,)
    assert (
        read_bing_page_performance(
            crawl_ingest, tenant_id=scope.tenant_id, site_id=scope.site_id
        ).observation.coverage
        == extra
    )


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 503])
async def test_failed_provider_never_commits_generation(crawl_ingest, admin, pages_context, status):
    scope, _, _, fetcher, _ = pages_context
    fetcher.status = status
    with pytest.raises(BingProtocolError):
        await import_pages(crawl_ingest, pages_context)
    assert admin.execute(
        "SELECT count(*) FROM app.bing_import_generations WHERE site_id=%s", (scope.site_id,)
    ).fetchone() == (0,)
    assert (
        read_bing_page_performance(crawl_ingest, tenant_id=scope.tenant_id, site_id=scope.site_id)
        is None
    )


@pytest.mark.anyio
async def test_revocation_during_read_rejects_commit(
    identity, identity_context, crawl_ingest, admin, pages_context
):
    scope, _, binding, fetcher, _ = pages_context
    fetcher.revoked = lambda: identity.execute(
        "SELECT outcome FROM control.revoke_bing_binding(%s,%s,%s,%s,%s)",
        (*session_args(identity_context, scope.site_id), binding, uuid4()),
    )
    with pytest.raises(BingBindingError, match="BING_IMPORT_REJECTED"):
        await import_pages(crawl_ingest, pages_context)
    assert admin.execute(
        "SELECT count(*) FROM app.bing_import_generations WHERE site_id=%s", (scope.site_id,)
    ).fetchone() == (0,)
