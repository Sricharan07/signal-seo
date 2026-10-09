import asyncio
import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_frontier import CrawlRunLimits
from signal_core.crawl_http import CrawlFetchUnavailable
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.egress_profiles import EgressProfile, PageSpeedScope
from signal_core.full_site_crawl import FullSiteCrawlExecutor
from signal_core.owner_connector_egress import OwnerConnectorContext
from signal_core.pagespeed import pagespeed_url, parse_pagespeed
from signal_core.pagespeed_collection import (
    PageSpeedUnavailable,
    collect_weekly_pagespeed,
    read_pagespeed,
    schedule_pagespeed,
)
from signal_core.pagespeed_credentials import OpenBaoPageSpeedCredentials
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider

from tests.connectors.test_pagespeed import KEY, document
from tests.control_plane.test_full_site_crawl import Fetcher, _command, _verify_origin


class PsiFetcher:
    def __init__(self, status=200, malformed=False):
        self.calls = []
        self.status = status
        self.malformed = malformed

    def request(self, request, *, policy):
        from urllib.parse import parse_qs, urlsplit

        self.calls.append(request)
        if request.url.endswith("/robots.txt"):
            body, media, status = b"", "text/plain", 404
        else:
            if self.status == 0:
                raise CrawlFetchUnavailable("Synthetic provider transport failure")
            params = parse_qs(urlsplit(request.url).query)
            body = (
                b"{"
                if self.malformed
                else json.dumps(document(params["url"][0], params["strategy"][0])).encode()
            )
            media, status = "application/json", self.status
        reference = Fetcher("https://www.googleapis.com").request(request, policy=policy)
        return replace(
            reference,
            http_status=status,
            media_type=media,
            response_headers=(),
            body=body,
            body_sha256=hashlib.sha256(body).hexdigest(),
            decoded_bytes=len(body),
        )


@pytest.fixture
def setup(admin, api, identity, scheduler, workflow, scopes, identity_context, tmp_path):
    scope = scopes[0]
    origin = _verify_origin(admin, identity, identity_context, scope)
    html = b"<title>Synthetic</title><h1>Home</h1>" + b"".join(
        f'<a href="/page-{number}">Page</a>'.encode() for number in range(6)
    )
    executor = FullSiteCrawlExecutor(
        admission_connection_factory=lambda: psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
        ),
        ingest_connection_factory=lambda: psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True
        ),
        store=EncryptedLocalArtifactStore(tmp_path / "crawl"),
        artifact_key=ArtifactEncryptionKey("synthetic-crawl-artifact-key", b"k" * 32),
        fetcher=Fetcher(origin, page_body=html),
        network_profile_sha256="ab" * 32,
        worker_key="worker.pagespeed-crawl",
        limits=CrawlRunLimits(max_urls=7, max_depth=1, max_duration_seconds=30),
    )
    command, first = _command(api, scheduler, workflow, scope, "pagespeed-sample")
    executor.run(command, first_run_id=first)
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (identity_context["identity_session_id"],),
    )
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE id=%s",
        (identity_context["tenant_session_id"],),
    )
    args = dict(
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
        site_id=scope.site_id,
    )
    with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as evidence:
        provider = SharedEgressProvider(
            identity,
            evidence,
            EncryptedLocalArtifactStore(tmp_path / "psi"),
            OwnerConnectorContext(
                scope.tenant_id,
                scope.site_id,
                args["session_token"],
                args["current_recovery_generation"],
            ),
            CrawlScopePolicy(
                1,
                ("https://www.googleapis.com",),
                "SignalBot/1.0 (+https://signal.example/bot)",
                max_redirects=0,
                max_body_bytes=512 * 1024,
                request_timeout_seconds=25,
                total_timeout_seconds=25,
            ),
            PsiFetcher(),
            "worker.pagespeed",
            OriginAdmissionPolicy(),
            ArtifactEncryptionKey("synthetic-pagespeed-artifact-key", b"k" * 32),
            purpose="connector",
        )
        yield provider, args, origin
        # Isolate subsequent fixed-provider fixtures after asserting this test's backoff.
        admin.execute(
            "UPDATE control.origin_buckets SET next_allowed_at=clock_timestamp(), "
            "degraded_until=NULL WHERE origin='https://www.googleapis.com'"
        )


def send(provider, sample, *, key=KEY):
    scope = PageSpeedScope(sample["verified_origin"], key)
    return provider.request_json(
        method="GET",
        url=pagespeed_url(scope, sample["url"], sample["strategy"]),
        profile=EgressProfile.PAGESPEED,
        pagespeed_scope=scope,
        authorization=None,
        operation_id=UUID(sample["sample_id"]),
        max_response_bytes=512 * 1024,
        timeout_seconds=25,
    )


def test_weekly_sample_is_bounded_replay_safe_and_falls_back_to_crawl_order(setup, identity, admin):
    provider, args, origin = setup
    first = schedule_pagespeed(identity, **args)
    assert first == schedule_pagespeed(identity, **args)
    rows = admin.execute(
        "SELECT url,strategy,selection_source FROM app.pagespeed_sample WHERE site_id=%s",
        (provider.run.site_id,),
    ).fetchall()
    assert len(rows) == 10 and len({r[0] for r in rows}) == 5
    assert {r[1] for r in rows} == {"mobile", "desktop"}
    assert {r[2] for r in rows} == {"crawl_order"}
    assert len(first["samples"]) == 4
    assert all(r[0].startswith(origin + "/") for r in rows)


def test_daily_collector_uses_real_shared_gateway_records_evidence_and_enforces_cap(
    setup, identity, admin
):
    provider, args, _ = setup
    credentials = OpenBaoPageSpeedCredentials(
        "https://bao.example.invalid", "synthetic-reader-token"
    )
    result = asyncio.run(
        collect_weekly_pagespeed(identity, egress=provider, credentials=credentials)
    )
    assert len(result["outcomes"]) == 4 and all(
        r["state"] == "observed" for r in result["outcomes"]
    )
    assert result["credential_mode"] == "keyless_quota"
    assert schedule_pagespeed(identity, **args)["samples"] == []
    read = read_pagespeed(identity, **args)
    assert len(read["samples"]) == 10
    assert sum(item["state"] == "observed" for item in read["samples"]) == 4
    assert read["local_lighthouse"] == "unavailable"
    assert len(provider.fetcher.calls) == 8
    rows = admin.execute(
        "SELECT request_url,response_evidence FROM app.owner_connector_egress_operations "
        "WHERE site_id=%s",
        (provider.run.site_id,),
    ).fetchall()
    assert KEY not in str(rows)
    assert all("key=" not in url for url, _ in rows)
    unattempted = admin.execute(
        "SELECT id,url,strategy,verified_origin FROM app.pagespeed_sample s "
        "WHERE site_id=%s AND NOT EXISTS(SELECT 1 FROM app.owner_connector_egress_operations e "
        "WHERE e.id=s.id) LIMIT 1",
        (provider.run.site_id,),
    ).fetchone()
    sample = dict(
        zip(("sample_id", "url", "strategy", "verified_origin"), unattempted, strict=True)
    )
    sample["sample_id"] = str(sample["sample_id"])
    with pytest.raises(ProviderEgressUnavailable, match="EGRESS_AUTHORITY_UNAVAILABLE"):
        send(provider, sample)
    assert len(provider.fetcher.calls) == 8


def test_optional_key_is_sent_once_but_never_persisted_and_unknown_is_not_retried(
    setup, identity, admin
):
    provider, args, _ = setup
    sample = schedule_pagespeed(identity, **args)["samples"][0]
    for _ in range(4):
        try:
            response = send(provider, sample)
            break
        except ProviderEgressUnavailable as error:
            assert error.code == "EGRESS_DEFERRED"
            asyncio.run(asyncio.sleep(1))
    assert response.status_code == 200
    assert any(KEY in r.url for r in provider.fetcher.calls)
    persisted = admin.execute(
        "SELECT to_jsonb(e) FROM app.owner_connector_egress_operations e WHERE site_id=%s",
        (provider.run.site_id,),
    ).fetchall()
    assert KEY not in str(persisted) and "key=" not in str(persisted)
    with pytest.raises(ProviderEgressUnavailable, match="EGRESS_BODY_NOT_RETAINED"):
        send(provider, sample)
    assert len(provider.fetcher.calls) == 2
    assert read_pagespeed(identity, **args)["samples"][0]["state"] == "outcome_unknown"


@pytest.mark.parametrize(
    ("status", "malformed", "reason"),
    [
        (429, False, "PSI_RATE_LIMITED"),
        (503, False, "PSI_PROVIDER_UNAVAILABLE"),
        (403, False, "PSI_CREDENTIAL_REJECTED"),
        (200, True, "PSI_RESPONSE_REJECTED"),
        (0, False, "PSI_RESPONSE_REJECTED"),
    ],
)
def test_failure_responses_stay_unavailable_never_zero(setup, identity, status, malformed, reason):
    provider, args, _ = setup
    provider.fetcher.status, provider.fetcher.malformed = status, malformed
    credentials = OpenBaoPageSpeedCredentials(
        "https://bao.example.invalid", "synthetic-reader-token"
    )
    result = asyncio.run(
        collect_weekly_pagespeed(identity, egress=provider, credentials=credentials)
    )
    assert result["outcomes"][0]["reason"] == reason
    samples = read_pagespeed(identity, **args)["samples"]
    assert samples[0]["state"] == "unavailable" and samples[0]["observation"] is None
    if status in {429, 503}:
        assert len(provider.fetcher.calls) == 2


@pytest.mark.parametrize(
    "change", ["tenant", "site", "role", "revoked", "generation", "unverified"]
)
def test_tenant_role_and_current_authority_negatives_do_not_dispatch(
    setup, identity, admin, change
):
    provider, args, _ = setup
    if change == "tenant":
        provider = replace(provider, run=replace(provider.run, tenant_id=uuid4()))
        sample = schedule_pagespeed(identity, **args)["samples"][0]
        with pytest.raises(ProviderEgressUnavailable, match="EGRESS_AUTHORITY_UNAVAILABLE"):
            send(provider, sample)
    else:
        if change == "site":
            args["site_id"] = uuid4()
        elif change == "role":
            admin.execute(
                "UPDATE app.memberships SET role_key='analyst' WHERE tenant_id=%s",
                (provider.run.tenant_id,),
            )
        elif change == "revoked":
            admin.execute(
                "UPDATE app.sessions SET revoked_at=clock_timestamp() WHERE active_site_id=%s",
                (provider.run.site_id,),
            )
        elif change == "generation":
            args["current_recovery_generation"] = "synthetic-stale-generation"
        else:
            admin.execute(
                "UPDATE app.sites SET primary_origin='https://other.invalid' WHERE id=%s",
                (provider.run.site_id,),
            )
        for function in (schedule_pagespeed, read_pagespeed):
            with pytest.raises(PageSpeedUnavailable, match="PSI_ACCESS_DENIED"):
                function(identity, **args)
    assert not provider.fetcher.calls


def test_observation_requires_exact_egress_evidence_and_immutable_function_only_rows(
    setup, identity, admin
):
    provider, args, _ = setup
    sample = schedule_pagespeed(identity, **args)["samples"][0]
    observation = parse_pagespeed(
        json.dumps(document(sample["url"], sample["strategy"])).encode(),
        url=sample["url"],
        strategy=sample["strategy"],
        evidence_id=UUID(sample["sample_id"]),
        fetched_at=datetime.now(UTC),
    )
    assert identity.execute(
        "SELECT control.finish_pagespeed(%s,%s,%s,%s,%s,%s,%s)",
        (
            hashlib.sha256(args["session_token"].encode()).digest(),
            args["current_recovery_generation"],
            args["site_id"],
            observation.evidence_id,
            Jsonb(observation.projection()),
            observation.response_sha256,
            None,
        ),
    ).fetchone() == ("egress_unavailable",)
    for table in ("pagespeed_sample", "pagespeed_observations", "pagespeed_failures"):
        assert admin.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
            ("app." + table,),
        ).fetchone() == (True, True)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            identity.execute(f"SELECT * FROM app.{table}")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute("DELETE FROM app.pagespeed_sample WHERE site_id=%s", (args["site_id"],))


@pytest.mark.parametrize("dimensions", [["page"], ["date", "page"]])
def test_top_pages_use_latest_current_gsc_clicks_with_generation_provenance(
    setup, identity, admin, dimensions
):
    provider, args, origin = setup
    session = (
        hashlib.sha256(args["session_token"].encode()).digest(),
        args["current_recovery_generation"],
        args["site_id"],
    )
    attempt, binding = uuid4(), uuid4()
    state = hashlib.sha256(f"synthetic-pagespeed-gsc-state-{attempt}".encode()).digest()
    callback = "https://dashboard.example.invalid/auth/gsc/callback"
    assert identity.execute(
        "SELECT control.begin_gsc_oauth_attempt(%s,%s,%s,%s,%s,%s,%s)",
        (*session, attempt, state, callback, "A" * 43),
    ).fetchone() == ("created",)
    assert identity.execute(
        "SELECT outcome FROM control.consume_gsc_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (*session, attempt, state, callback),
    ).fetchone() == ("consumed",)
    assert identity.execute(
        "SELECT control.stage_gsc_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (
            *session,
            attempt,
            f"secret://gsc/{attempt}",
            Jsonb(
                [{"resource_name": origin + "/", "property_type": "url_prefix", "eligible": True}]
            ),
        ),
    ).fetchone() == ("staged",)
    assert identity.execute(
        "SELECT control.confirm_gsc_binding(%s,%s,%s,%s,%s,%s,%s)",
        (*session, attempt, binding, uuid4(), origin + "/"),
    ).fetchone() == ("bound",)
    generation = uuid4()
    keys = [origin + "/page-5"] if dimensions == ["page"] else ["2026-09-28", origin + "/page-5"]
    admin.execute(
        "INSERT INTO app.gsc_import_generations(tenant_id,site_id,id,binding_id,"
        "property_resource_name,search_type,dimensions,start_date,end_date,data_state,aggregation_type,"
        "rows,coverage,response_sha256,egress_operation_id) VALUES(%s,%s,%s,%s,%s,'web',%s,"
        "current_date-28,current_date-1,'all','byPage',%s,%s,%s,%s)",
        (
            provider.run.tenant_id,
            provider.run.site_id,
            generation,
            binding,
            origin + "/",
            Jsonb(dimensions),
            Jsonb([{"keys": keys, "clicks": 99}]),
            Jsonb({"complete": False, "missing_data": "provider_top_rows_only"}),
            b"s" * 32,
            uuid4(),
        ),
    )
    scheduled = schedule_pagespeed(identity, **args)
    assert scheduled["samples"][0]["url"] == origin + "/page-5"
    assert scheduled["samples"][0]["selection_source"] == "gsc_clicks"
    assert admin.execute(
        "SELECT DISTINCT gsc_generation_id FROM app.pagespeed_sample WHERE site_id=%s",
        (provider.run.site_id,),
    ).fetchall() == [(generation,)]


def test_database_projection_rejects_estimates_secret_fields_and_mismatched_ratings(
    setup, identity, admin
):
    import copy

    provider, args, _ = setup
    sample = schedule_pagespeed(identity, **args)["samples"][0]
    observation = parse_pagespeed(
        json.dumps(document(sample["url"], sample["strategy"])).encode(),
        url=sample["url"],
        strategy=sample["strategy"],
        evidence_id=UUID(sample["sample_id"]),
        fetched_at=datetime.now(UTC),
    )
    projection = observation.projection()
    assert admin.execute(
        "SELECT control.valid_pagespeed_projection(%s)", (Jsonb(projection),)
    ).fetchone() == (True,)
    for change in (
        "extra",
        "secret",
        "estimate",
        "rating",
        "collection_period",
        "field_status",
        "lab_status",
    ):
        value = copy.deepcopy(projection)
        if change == "extra":
            value["lab"]["key"] = KEY
        elif change == "secret":
            value["findings"][0]["id"] = KEY
        elif change == "estimate":
            value["field_url"]["metrics"]["lcp"]["state"] = "unavailable"
            value["field_url"]["metrics"]["lcp"]["reason"] = "insufficient_field_data"
        elif change == "rating":
            value["field_url"]["metrics"]["lcp"]["rating"] = "good"
        elif change == "collection_period":
            value["field_origin"]["collection_period"]["first_date"] = "secret-instead-of-date"
        elif change == "field_status":
            value["field_origin"]["status"] = "good"
        else:
            value["lab"]["status"] = "unavailable"
        assert admin.execute(
            "SELECT control.valid_pagespeed_projection(%s)", (Jsonb(value),)
        ).fetchone() == (False,)
