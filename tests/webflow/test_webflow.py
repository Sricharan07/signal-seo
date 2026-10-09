import copy
import hashlib
import json
import os
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx2
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg import sql
from psycopg.types.json import Jsonb
from signal_core.authority_journal import AuthorityJournal, dispatch_pending_restrictions
from signal_core.business_brain import (
    FactCategory,
    FactProvenance,
    approve_fact,
    propose_fact,
    read_brain,
)
from signal_core.candidate_build_service import dispatch_candidate_build, finish_candidate_build
from signal_core.content_writer import claim_question
from signal_core.content_writer_service import writer_call
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_http import EgressHttpRequest, EgressHttpResult
from signal_core.decision_contracts import canonical_json
from signal_core.decision_records import PostgresDecisionRecorder
from signal_core.egress_profiles import (
    EgressProfile,
    WebflowScope,
    profile_headers,
    validate_profile_request,
)
from signal_core.jev_decisions import DecisionService, JevHttpAdapter
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import SharedEgressProvider
from signal_core.webflow import (
    ATOMIC_PUBLISH_PRECONDITION,
    ATOMIC_UPDATE_PRECONDITION,
    CAPABILITIES,
    SCOPES,
    WebflowUnavailable,
    draft_body,
    published_domain,
    reconcile_items,
    validate_grant,
    validate_mapping,
)
from signal_core.webflow_secrets import OpenBaoWebflowSecrets
from signal_core.webflow_service import WebflowService
from signal_core.write_intent_journal import WebflowWriteIntentRecord, WriteIntentJournal

from tests.control_plane.test_candidate_builds import _active_extension, _prepare, _result
from tests.control_plane.test_shared_egress import authority

TOKEN = "synthetic-webflow-access-token-0098"
MAPPING = {"title": "name", "description": "description", "body": "body"}
SCHEMA = [
    {"slug": name, "type": kind, "isRequired": required, "isEditable": True, "validations": {}}
    for name, kind, required in [
        ("name", "PlainText", True),
        ("slug", "PlainText", True),
        ("description", "PlainText", False),
        ("body", "RichText", False),
    ]
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Bao:
    def __init__(self):
        self.documents = {
            "client": {
                "client_id": "synthetic-webflow-client-id-0098",
                "client_secret": "synthetic-webflow-client-secret-0098",
            }
        }

    def request(self, request):
        key = request.url.path.split("/data/")[-1]
        if request.method == "DELETE":
            self.documents.pop("tokens/" + request.url.path.rsplit("/", 1)[-1], None)
            return httpx2.Response(204)
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["options"] == {"cas": 0} and key not in self.documents
            self.documents[key] = payload["data"]
            return httpx2.Response(200, json={"data": {"version": 1}})
        if key not in self.documents:
            return httpx2.Response(404)
        return httpx2.Response(
            200,
            json={
                "data": {
                    "data": self.documents[key],
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )


class WebflowDouble:
    def __init__(self, origin, provider_site, collection):
        self.origin, self.site, self.collection = origin, provider_site, collection
        self.scopes = ",".join(sorted(SCOPES))
        self.domains = [
            {"url": origin.removeprefix("https://"), "lastPublished": "2026-01-01T00:00:00Z"}
        ]
        self.collections = [{"id": collection}]
        self.schema = copy.deepcopy(SCHEMA)
        self.items, self.delayed = [], []
        self.calls = []
        self.ambiguous = False
        self.delay_commit = False
        self.resolved_address = None

    def request(self, outbound, *, policy):
        self.calls.append((outbound.method, outbound.url, outbound.body))
        path = urlsplit(outbound.url).path
        if path == "/oauth/access_token":
            assert json.loads(outbound.body)["client_secret"].startswith("synthetic-")
            payload = {"access_token": TOKEN}
        elif path == "/v2/token/introspect":
            payload = {
                "authorization": {
                    "grantType": "authorization_code",
                    "scope": self.scopes,
                    "authorizedTo": {"siteIds": [self.site], "workspaceIds": [], "userIds": []},
                }
            }
        elif path.endswith("/custom_domains"):
            payload = {"customDomains": self.domains}
        elif path.endswith("/collections"):
            payload = {"collections": self.collections}
        elif path == f"/v2/collections/{self.collection}":
            payload = {"id": self.collection, "fields": self.schema}
        elif path.endswith("/items/insert"):
            data = json.loads(outbound.body)
            assert set(data) == {"items"} and len(data["items"]) == 1
            assert data["items"][0]["isDraft"] is True and "isArchived" not in data["items"][0]
            item = {
                **data["items"][0],
                "id": uuid4().hex[:24],
                "isArchived": False,
                "lastPublished": None,
            }
            (self.delayed if self.delay_commit else self.items).append(item)
            if self.ambiguous:
                return EgressHttpResult(
                    1,
                    outbound.url,
                    outbound.url,
                    outbound.method,
                    "transport_error",
                    None,
                    None,
                    (),
                    None,
                    b"",
                    None,
                    0,
                    1,
                )
            payload = {"items": [item]}
        elif path.endswith("/items"):
            slug = parse_qs(urlsplit(outbound.url).query)["slug"][0]
            matched = [i for i in self.items if i["fieldData"]["slug"] == slug]
            payload = {
                "items": matched[:2],
                "pagination": {"total": len(matched), "limit": 2, "offset": 0},
            }
        else:
            raise AssertionError("Unexpected Webflow route")
        body = canonical_json(payload)
        return EgressHttpResult(
            1,
            outbound.url,
            outbound.url,
            outbound.method,
            "fetched",
            202 if path.endswith("/insert") else 200,
            "application/json",
            (("content-type", "application/json"),),
            self.resolved_address,
            body,
            hashlib.sha256(body).hexdigest(),
            len(body),
            1,
        )


@pytest.fixture(scope="session")
def journal():
    key = Ed25519PrivateKey.generate()
    with psycopg.connect(
        os.environ["SIGNAL_TEST_WRITE_JOURNAL_DSN"], autocommit=True
    ) as connection:
        yield WriteIntentJournal(connection, key.public_key(), b"w" * 64, key)


@pytest.fixture(scope="session")
def restriction_journal():
    key = Ed25519PrivateKey.generate()
    with psycopg.connect(
        os.environ["SIGNAL_TEST_WRITE_JOURNAL_DSN"], autocommit=True
    ) as connection:
        yield AuthorityJournal(connection, key.public_key(), b"r" * 64, key)


async def source(admin, api, identity, scopes, context, *, approved=True):
    scope, context, extension = await _active_extension(admin, identity, scopes, context)
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (context["identity_session_id"],),
    )
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE session_token_hash=%s",
        (hash_session_token(context["session_token"]),),
    )
    origin = admin.execute(
        "SELECT primary_origin FROM app.sites WHERE id=%s", (scope.site_id,)
    ).fetchone()[0]
    common = {
        "session_token": context["session_token"],
        "generation": context["generation"],
        "site_id": scope.site_id,
    }
    propose_fact(
        api,
        session_token=common["session_token"],
        current_recovery_generation=common["generation"],
        site_id=scope.site_id,
        category=FactCategory.AUDIENCE,
        statement="Founders are our audience.",
        provenance=FactProvenance("owner_statement"),
    )
    fid = admin.execute(
        "SELECT id FROM app.business_brain_facts WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone()[0]
    approve_fact(
        api,
        session_token=common["session_token"],
        current_recovery_generation=common["generation"],
        site_id=scope.site_id,
        fact_id=fid,
    )
    facts = read_brain(
        api,
        session_token=common["session_token"],
        current_recovery_generation=common["generation"],
        site_id=scope.site_id,
    )["facts"]
    brief, draft, candidate = uuid4(), uuid4(), uuid4()
    admin.execute(
        "INSERT INTO app.content_briefs(tenant_id,site_id,id,payload,origin,created_by) "
        "VALUES(%s,%s,%s,%s,'owner',%s)",
        (
            scope.tenant_id,
            scope.site_id,
            brief,
            Jsonb({"kind": "new_article", "fact_ids": [str(fid)]}),
            context["user_id"],
        ),
    )
    admin.execute(
        "INSERT INTO app.content_draft_intents(tenant_id,site_id,id,brief_id,writer_version,"
        "input_sha256,fact_snapshot) VALUES(%s,%s,%s,%s,'content-writer-v1',%s,%s)",
        (scope.tenant_id, scope.site_id, draft, brief, b"i" * 32, Jsonb(facts)),
    )
    tagged = {"text": "Founders are our audience.", "fact_ids": [str(fid)]}
    article = {
        "title": tagged,
        "meta_description": tagged,
        "sections": [{"heading": tagged, "sentences": [tagged]}],
        "internal_links": [],
    }
    output = {
        "article": article,
        "state": "grounded",
        "originality": {"state": "original"},
        "grounding": {"state": "grounded"},
    }
    encoded = canonical_json(output)
    with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as recorder:
        decision = await DecisionService(
            JevHttpAdapter(None), PostgresDecisionRecorder(recorder, scope)
        ).recommend(claim_question(article, facts))
    assert (
        writer_call(
            api,
            *common.values(),
            "finish_draft",
            draft,
            encoded,
            hashlib.sha256(encoded).digest(),
            decision.decision_id,
        )
        == "completed"
    )
    build = _prepare(identity, scope, context, extension)
    dispatch_candidate_build(
        identity,
        session_token=common["session_token"],
        current_recovery_generation=common["generation"],
        prepared=build,
    )
    finish_candidate_build(
        identity,
        session_token=common["session_token"],
        current_recovery_generation=common["generation"],
        prepared=build,
        result=_result(),
    )
    manifest = {
        "approval_class": "A2",
        "threshold": 0.95,
        "autonomy_eligible": False,
        "work_type": "new_article",
        "base_sha": build.base_sha,
        "patch_sha256": build.patch_sha256,
        "changed_files": [{"path": "article.html", "before": "", "after": "Synthetic article"}],
    }
    encoded = canonical_json(manifest)
    result = writer_call(
        api,
        *common.values(),
        "seal",
        candidate,
        draft,
        extension.id,
        build.id,
        encoded,
        hashlib.sha256(encoded).digest(),
    )
    assert result["state"] == "sealed"
    if approved:
        admin.execute(
            "INSERT INTO app.content_delivery_decisions "
            "SELECT %s,%s,%s,%s,%s,a.user_id,%s,a.membership_epoch,a.site_authorization_epoch,"
            "'dashboard','[]'::jsonb,clock_timestamp(),clock_timestamp() "
            "FROM control.resolve_snapshot_authority(%s,%s,%s) a",
            (
                scope.tenant_id,
                scope.site_id,
                uuid4(),
                candidate,
                hashlib.sha256(encoded).digest(),
                common["generation"],
                hash_session_token(common["session_token"]),
                scope.site_id,
                common["generation"],
            ),
        )
    return scope, common, origin, candidate, hashlib.sha256(encoded).hexdigest()


def provider(api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path, double):
    store = EncryptedLocalArtifactStore(tmp_path / uuid4().hex)
    run, policy, snapshot = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "webflow-" + uuid4().hex,
        store,
        origin_override="https://api.webflow.com",
        verification_profile=True,
    )
    double.resolved_address = snapshot.resolved_address
    return SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        double,
        "webflow.test",
        OriginAdmissionPolicy(),
        None,
        "connector",
    )


async def bind(service, egress, double, common):
    begun = await service.begin(
        session_token=common["session_token"],
        site_id=common["site_id"],
        provider_site=double.site,
        collection_id=double.collection,
    )
    query = parse_qs(urlsplit(begun["authorization_url"]).query)
    assert set(query["scope"][0].split(" ")) == SCOPES
    result = await service.complete(
        session_token=common["session_token"],
        site_id=common["site_id"],
        attempt_id=UUID(begun["attempt_id"]),
        state=query["state"][0],
        code="synthetic-webflow-code-0098",
        field_mapping=MAPPING,
        egress=egress,
    )
    return UUID(result["id"])


def service(api, context, journal=None):
    bao = Bao()
    return WebflowService(
        api,
        OpenBaoWebflowSecrets("https://bao.example.invalid", "synthetic-webflow-bao-token"),
        "https://dashboard.example.invalid",
        context["generation"],
        journal,
        {"transport": httpx2.MockTransport(bao.request)},
        True,
    ), bao


def test_research_contract_does_not_invent_cas_or_owner_publish():
    assert ATOMIC_UPDATE_PRECONDITION is False and ATOMIC_PUBLISH_PRECONDITION is False
    assert "ATOMIC_PRECONDITION_UNAVAILABLE" in CAPABILITIES["update"]
    assert "ATOMIC_PRECONDITION_UNAVAILABLE" in CAPABILITIES["publish"]
    assert "NOT_EXECUTED" in CAPABILITIES["production"]


@pytest.mark.parametrize("field", ["isDraft", "isArchived", "cmsLocaleIds", "id"])
def test_only_closed_drafts_pass_the_profile(field):
    op = uuid4()
    tagged = {"text": "Synthetic article.", "fact_ids": [str(uuid4())]}
    payload = draft_body(
        {
            "title": tagged,
            "meta_description": tagged,
            "sections": [{"heading": tagged, "sentences": [tagged]}],
            "internal_links": [],
        },
        MAPPING,
        op,
    )
    item = payload["items"][0]
    item[field] = False if field == "isDraft" else True
    body = canonical_json(payload)
    scope = WebflowScope("a" * 24, "b" * 24, TOKEN, hashlib.sha256(body).hexdigest())
    request = EgressHttpRequest(
        "POST",
        "https://api.webflow.com/v2/collections/" + scope.collection_id + "/items/insert",
        headers=profile_headers(
            EgressProfile.WEBFLOW, method="POST", authorization="Bearer " + TOKEN
        ),
        body=body,
        timeout_seconds=5,
        max_response_bytes=131072,
    )
    with pytest.raises(ValueError):
        validate_profile_request(
            EgressProfile.WEBFLOW, "connector", request, False, webflow_scope=scope
        )


@pytest.mark.parametrize(
    "method,path",
    [
        ("DELETE", "/items/abc"),
        ("PATCH", "/items/abc"),
        ("POST", "/items/publish"),
        ("POST", "/items/live"),
        ("POST", "/fields"),
        ("GET", "/items/live"),
        ("GET", "/items?limit=100"),
        ("POST", "/items"),
    ],
)
def test_no_archive_delete_design_publish_or_legacy_endpoints(method, path):
    scope = WebflowScope("a" * 24, "b" * 24, TOKEN)
    with pytest.raises(ValueError):
        request = EgressHttpRequest(
            method,
            "https://api.webflow.com/v2/collections/" + scope.collection_id + path,
            headers=profile_headers(
                EgressProfile.WEBFLOW, method=method, authorization="Bearer " + TOKEN
            ),
            timeout_seconds=5,
            max_response_bytes=131072,
        )
        validate_profile_request(
            EgressProfile.WEBFLOW, "connector", request, False, webflow_scope=scope
        )


@pytest.mark.parametrize(
    "scopes",
    [
        "cms:read",
        "cms:read,cms:write,sites:read,pages:write",
        "cms:read,cms:write,sites:write",
        "cms:read,cms:write,sites:read,sites:read",
    ],
)
def test_scope_negatives(scopes):
    with pytest.raises(WebflowUnavailable, match="WEBFLOW_OAUTH_SCOPE_REJECTED"):
        validate_grant(
            {
                "authorization": {
                    "grantType": "authorization_code",
                    "scope": scopes,
                    "authorizedTo": {"siteIds": ["a" * 24]},
                }
            },
            "a" * 24,
        )


@pytest.mark.parametrize(
    "mapping", [{**MAPPING, "body": []}, {**MAPPING, "body": "name"}, {**MAPPING, "extra": "slug"}]
)
def test_owner_field_mapping_is_closed(mapping):
    with pytest.raises(WebflowUnavailable):
        validate_mapping(mapping, SCHEMA)


def test_required_extra_field_and_unpublished_domain_refused():
    with pytest.raises(WebflowUnavailable):
        validate_mapping(MAPPING, [*SCHEMA, {"slug": "image", "type": "Image", "isRequired": True}])
    with pytest.raises(WebflowUnavailable):
        published_domain(
            {"customDomains": [{"url": "example.invalid", "lastPublished": None}]},
            "https://example.invalid",
        )


@pytest.mark.anyio
@pytest.mark.parametrize("delayed", [False, True])
async def test_one_post_ambiguity_zero_read_and_delayed_commit(
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
    journal,
    delayed,
    monkeypatch,
):
    from signal_core.webflow_service import _OneShotFetcher

    fence_errors = []
    original_fence = _OneShotFetcher.request

    def observe_fence(self, request, *, policy):
        try:
            return original_fence(self, request, policy=policy)
        except Exception as error:
            fence_errors.append(str(error))
            raise

    monkeypatch.setattr(_OneShotFetcher, "request", observe_fence)
    scope, common, origin, candidate, digest = await source(
        admin, api, identity, scopes, identity_context
    )
    svc, bao = service(api, identity_context, journal)
    double = WebflowDouble(origin, uuid4().hex[:24], uuid4().hex[:24])
    egress = provider(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path, double
    )
    binding = await bind(svc, egress, double, common)
    sealed = svc.seal(
        session_token=common["session_token"],
        site_id=scope.site_id,
        binding_id=binding,
        candidate_id=candidate,
        source_sha256=digest,
    )
    revision = UUID(sealed["id"])
    packet = svc.call(common["session_token"], scope.site_id, "packet", {"id": str(revision)})
    assert svc.review(
        session_token=common["session_token"],
        site_id=scope.site_id,
        revision_id=revision,
        revision_sha256=packet["revision_sha256"],
        decision="approved",
    ) == {"state": "reviewed"}
    double.ambiguous, double.delay_commit = True, delayed
    args = {
        "session_token": common["session_token"],
        "site_id": scope.site_id,
        "revision_id": revision,
        "egress": egress,
    }
    result = await svc.deliver(**args)
    assert not fence_errors
    assert sum(m == "POST" and "/items/insert" in u for m, u, _ in double.calls) == 1, (
        admin.execute(
            "SELECT state,network_outcome FROM app.egress_operations WHERE id=%s", (revision,)
        ).fetchall()
    )
    assert result["state"] == ("OUTCOME_UNKNOWN" if delayed else "DRAFT_RECORDED")
    assert sum(m == "POST" and "/items/insert" in u for m, u, _ in double.calls) == 1
    assert (await svc.deliver(**args))["state"] == result["state"]
    double.items.extend(double.delayed)
    assert (await svc.deliver(**args))["state"] == "DRAFT_RECORDED"
    assert sum(m == "POST" and "/items/insert" in u for m, u, _ in double.calls) == 1
    _, entries = journal.verify_stream()
    assert isinstance(entries[-1][0], WebflowWriteIntentRecord)
    rows = admin.execute(
        "SELECT to_jsonb(b)::text FROM app.webflow_bindings b WHERE id=%s", (binding,)
    ).fetchone()[0]
    assert TOKEN not in rows and "access_token" not in rows
    all_ops = admin.execute(
        "SELECT to_jsonb(o)::text FROM app.egress_operations o WHERE tenant_id=%s",
        (scope.tenant_id,),
    ).fetchall()
    assert all(
        TOKEN not in row[0] and "synthetic-webflow-client-secret" not in row[0] for row in all_ops
    )
    assert TOKEN not in repr(svc) and TOKEN not in repr(egress)
    assert any(TOKEN in str(v) for v in bao.documents.values())


@pytest.mark.anyio
@pytest.mark.parametrize("negative", ["scope", "site", "collection", "format"])
async def test_provider_binding_mismatches_refused(
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
    negative,
):
    scope, common, origin, _, _ = await source(admin, api, identity, scopes, identity_context)
    svc, bao = service(api, identity_context)
    double = WebflowDouble(origin, uuid4().hex[:24], uuid4().hex[:24])
    if negative == "scope":
        double.scopes += ",sites:write"
    if negative == "site":
        double.domains[0]["url"] = "wrong.example.invalid"
    if negative == "collection":
        double.collections = []
    if negative == "format":
        double.schema[-1]["type"] = "Image"
    egress = provider(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path, double
    )
    with pytest.raises(WebflowUnavailable):
        await bind(svc, egress, double, common)
    assert list(bao.documents) == ["client"]
    assert not admin.execute(
        "SELECT 1 FROM app.webflow_bindings WHERE tenant_id=%s", (scope.tenant_id,)
    ).fetchall()


def test_duplicate_reconciliation_escalates_and_zero_never_unapplied():
    assert reconcile_items({"items": [], "pagination": {"total": 0}}, {}) == (
        "OUTCOME_UNKNOWN",
        None,
    )
    assert reconcile_items({"items": [{}, {}], "pagination": {"total": 2}}, {}) == (
        "ESCALATED",
        None,
    )


@pytest.mark.parametrize("role", ["analyst", "admin", "owner"])
def test_tenant_role_and_direct_table_negatives(admin, api, scopes, identity_context, role):
    admin.execute(
        "UPDATE app.memberships SET role_key=%s WHERE id=%s",
        (role, identity_context["membership_id"]),
    )
    svc, _ = service(api, identity_context)
    for scope in scopes if role != "owner" else scopes[1:]:
        with pytest.raises(WebflowUnavailable):
            svc.read(session_token=identity_context["session_token"], site_id=scope.site_id)
    for table in ("webflow_bindings", "webflow_operations", "webflow_reviews"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(sql.SQL("SELECT * FROM app.{}").format(sql.Identifier(table)))


@pytest.mark.anyio
async def test_production_gate_refuses_without_any_network(api, identity_context):
    svc, _ = service(api, identity_context)
    svc = replace(svc, disposable_test_writes=False)
    with pytest.raises(WebflowUnavailable, match="NOT_EXECUTED"):
        await svc.deliver(
            session_token=identity_context["session_token"],
            site_id=uuid4(),
            revision_id=uuid4(),
            egress=None,
        )


@pytest.fixture
async def prepared(
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
    journal,
):
    scope, common, origin, candidate, digest = await source(
        admin, api, identity, scopes, identity_context
    )
    svc, bao = service(api, identity_context, journal)
    double = WebflowDouble(origin, uuid4().hex[:24], uuid4().hex[:24])
    egress = provider(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scope, tmp_path, double
    )
    binding = await bind(svc, egress, double, common)
    args = {"session_token": common["session_token"], "site_id": scope.site_id}
    sealed = svc.seal(**args, binding_id=binding, candidate_id=candidate, source_sha256=digest)
    assert svc.seal(**args, binding_id=binding, candidate_id=candidate, source_sha256=digest) == {
        "state": "replayed",
        "id": sealed["id"],
    }
    revision = UUID(sealed["id"])
    packet = svc.call(common["session_token"], scope.site_id, "packet", {"id": str(revision)})
    return svc, bao, double, egress, args, revision, binding, packet


@pytest.mark.anyio
@pytest.mark.parametrize(
    "negative", ["no_approval", "wrong_digest", "rejected", "membership_epoch", "schema", "journal"]
)
async def test_approval_revision_and_failure_denials_have_no_post(
    prepared, admin, identity_context, negative
):
    svc, _, double, egress, args, revision, _, packet = prepared
    if negative == "wrong_digest":
        with pytest.raises(WebflowUnavailable):
            svc.review(**args, revision_id=revision, revision_sha256="0" * 64, decision="approved")
    elif negative != "no_approval":
        svc.review(
            **args,
            revision_id=revision,
            revision_sha256=packet["revision_sha256"],
            decision="rejected" if negative == "rejected" else "approved",
        )
    if negative == "membership_epoch":
        admin.execute(
            "UPDATE app.memberships SET authorization_epoch=authorization_epoch+1 WHERE id=%s",
            (identity_context["membership_id"],),
        )
    elif negative == "schema":
        double.schema[-1]["isRequired"] = True
    elif negative == "journal":
        svc = replace(svc, write_journal=None)
    with pytest.raises(WebflowUnavailable) as error:
        await svc.deliver(**args, revision_id=revision, egress=egress)
    assert TOKEN not in str(error.value)
    assert not any(m == "POST" and "/items/insert" in u for m, u, _ in double.calls)


@pytest.mark.anyio
async def test_restored_primary_cannot_retry_intent_and_duplicate_is_sticky(prepared, admin):
    svc, _, double, egress, args, revision, binding, packet = prepared
    svc.review(
        **args, revision_id=revision, revision_sha256=packet["revision_sha256"], decision="approved"
    )
    record = WebflowWriteIntentRecord(
        revision,
        args["site_id"],
        binding,
        double.site,
        double.collection,
        packet["revision_sha256"],
        packet["payload"]["items"][0]["fieldData"]["slug"],
    )
    svc.write_journal.append(record)
    assert (await svc.deliver(**args, revision_id=revision, egress=egress))[
        "state"
    ] == "OUTCOME_UNKNOWN"
    assert not any(m == "POST" and "/items/insert" in u for m, u, _ in double.calls)
    double.items = [
        {
            **packet["payload"]["items"][0],
            "id": uuid4().hex[:24],
            "isArchived": False,
            "lastPublished": None,
        }
        for _ in range(2)
    ]
    assert (await svc.reconcile(**args, revision_id=revision, egress=egress))[
        "state"
    ] == "ESCALATED"
    double.items = double.items[:1]
    assert (await svc.reconcile(**args, revision_id=revision, egress=egress))[
        "state"
    ] == "ESCALATED"
    assert (
        admin.execute(
            "SELECT state FROM app.webflow_operations WHERE id=%s", (revision,)
        ).fetchone()[0]
        == "ESCALATED"
    )


@pytest.mark.anyio
async def test_local_revocation_journal_ack_and_missing_target_replay(
    prepared, restriction_journal, admin
):
    svc, bao, _, _, args, _, binding, _ = prepared
    result = await svc.revoke(**args, binding_id=binding)
    assert result["state"] == "AUTHORITY_DURABILITY_PENDING"
    assert list(bao.documents) == ["client"]
    with pytest.raises(WebflowUnavailable):
        svc.call(args["session_token"], args["site_id"], "binding", {"binding_id": str(binding)})
    with psycopg.connect(
        os.environ["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"], autocommit=True
    ) as dispatcher:
        assert dispatch_pending_restrictions(dispatcher, restriction_journal) >= 1
        stream = restriction_journal.verify_stream()
        record = next(record for record, _ in stream.entries if record.target_id == binding)
        assert (
            record.target_kind == "webflow_binding"
            and record.restriction_kind == "webflow_binding_revoked"
        )
        missing = uuid4()
        dispatcher.execute(
            "SELECT control.apply_webflow_authority_denial(%s,%s,1,%s,%s,%s)",
            (uuid4(), missing, stream.generation, stream.head_position, "a" * 64),
        )
    assert (
        svc.call(args["session_token"], args["site_id"], "revoke", {"id": str(binding)})["state"]
        == "revoked"
    )
    assert (
        admin.execute(
            "SELECT restriction_kind FROM control.authority_denial_tombstones WHERE target_id=%s",
            (missing,),
        ).fetchone()[0]
        == "webflow_binding_revoked"
    )


@pytest.mark.anyio
async def test_oauth_state_one_use_and_token_errors_redacted(prepared):
    svc, _, double, egress, args, _, _, _ = prepared
    begun = await svc.begin(**args, provider_site=double.site, collection_id=double.collection)
    state = parse_qs(urlsplit(begun["authorization_url"]).query)["state"][0]
    complete = {
        **args,
        "attempt_id": UUID(begun["attempt_id"]),
        "code": "synthetic-webflow-code-0098",
        "field_mapping": MAPPING,
        "egress": egress,
    }
    with pytest.raises(WebflowUnavailable):
        await svc.complete(**complete, state="synthetic-wrong-state")
    # Existing one-site binding refuses a second binding; its consumed callback
    # must still be unusable, and cleanup must not expose any token material.
    for _ in range(2):
        with pytest.raises(WebflowUnavailable) as error:
            await svc.complete(**complete, state=state)
        assert TOKEN not in str(error.value)
    assert sum(m == "POST" and "/oauth/access_token" in u for m, u, _ in double.calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("negative", ["pause", "membership_epoch"])
async def test_last_network_fence_rechecks_authority(
    prepared, admin, identity_context, monkeypatch, negative
):
    from signal_core.webflow_service import _OneShotFetcher

    svc, _, double, egress, args, revision, _, packet = prepared
    svc.review(
        **args, revision_id=revision, revision_sha256=packet["revision_sha256"], decision="approved"
    )
    original = _OneShotFetcher.request
    fences = []

    def restrict(self, request, *, policy):
        fences.append(self.operation_id)
        if negative == "membership_epoch":
            admin.execute(
                "UPDATE app.memberships SET authorization_epoch=authorization_epoch+1 WHERE id=%s",
                (identity_context["membership_id"],),
            )
        else:
            admin.execute(
                "INSERT INTO app.site_weekly_control(tenant_id,site_id,paused) "
                "SELECT tenant_id,id,true FROM app.sites WHERE id=%s "
                "ON CONFLICT(tenant_id,site_id) DO UPDATE SET paused=true",
                (args["site_id"],),
            )
        return original(self, request, policy=policy)

    monkeypatch.setattr(_OneShotFetcher, "request", restrict)
    if negative == "pause":
        with pytest.raises(WebflowUnavailable):
            await svc.deliver(**args, revision_id=revision, egress=egress)
    else:
        assert (await svc.deliver(**args, revision_id=revision, egress=egress))[
            "state"
        ] == "OUTCOME_UNKNOWN"
    assert fences == [revision]
    assert not any(m == "POST" and "/items/insert" in u for m, u, _ in double.calls)
    assert any(
        record.operation_id == revision for record, _ in svc.write_journal.verify_stream()[1]
    )
