"""Real primary/journal PostgreSQL and loopback Core REST contract double."""

import hashlib
import http.client
import json
import os
import threading
import time
from contextlib import closing
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx2
import psycopg
import pytest
import rfc8785
from psycopg.types.json import Jsonb
from signal_core.business_brain import FactCategory, FactProvenance, approve_fact, propose_fact
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_http import EgressHttpResult
from signal_core.live_verification import SharedLiveVerifier
from signal_core.shared_egress import SharedEgressProvider
from signal_core.wordpress_protocol import WordPressCredential, WordPressUnavailable
from signal_core.wordpress_secrets import OpenBaoWordPressSecrets
from signal_core.wordpress_service import WordPressService, wordpress_call
from signal_core.write_intent_journal import WordPressWriteIntentRecord, WriteIntentJournal

from tests.control_plane.test_full_site_crawl import _verify_origin
from tests.control_plane.test_shared_egress import authority
from tests.delivery.autonomy_delivery_support import JOURNAL_ENCRYPTION_KEY as _ENCRYPTION
from tests.delivery.autonomy_delivery_support import JOURNAL_SIGNING_KEY as _KEY

_CREDENTIAL_READ = OpenBaoWordPressSecrets.credential


def article(fact_id):
    tagged = {"text": "Founders are our audience.", "fact_ids": [str(fact_id)]}
    return {
        "title": tagged,
        "meta_description": tagged,
        "sections": [{"heading": tagged, "sentences": [tagged]}],
        "internal_links": [],
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


class CoreDouble:
    def __init__(self):
        self.user = {
            "id": 17,
            "roles": ["author"],
            "capabilities": {"read": True, "edit_posts": True, "author": True},
        }
        self.posts = []
        self.pending = []
        self.calls = []
        self.mode = "normal"
        state = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                state.calls.append(("GET", self.path, None))
                path = urlsplit(self.path)
                if not path.path.startswith("/wp-json/"):
                    body = (
                        "<html><body>" + state.posts[0]["content"]["raw"] + "</body></html>"
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path.path.endswith("users/me"):
                    value = state.user
                elif path.path.endswith("/posts"):
                    slug = parse_qs(path.query)["slug"][0]
                    value = [p for p in state.posts if p["slug"] == slug and p["status"] == "draft"]
                else:
                    value = next(
                        p for p in state.posts if str(p["id"]) == path.path.rsplit("/", 1)[1]
                    )
                self.reply(200, value)

            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state.calls.append(("POST", self.path, value))
                assert self.path == "/wp-json/wp/v2/posts" and value["status"] == "draft"
                assert (
                    self.headers["Authorization"]
                    == WordPressCredential(
                        "synthetic-author", "synthetic-application-password"
                    ).authorization
                )
                if state.mode == "rejected":
                    self.reply(400, {"code": "rest_invalid_param"})
                    return
                result = {
                    "id": len(state.posts) + len(state.pending) + 1,
                    "author": 17,
                    "slug": value["slug"],
                    "status": "draft",
                    "modified_gmt": "2026-01-01T00:00:00",
                    "link": state.origin + "/" + value["slug"] + "/",
                    **{k: {"raw": value[k]} for k in ("title", "content", "excerpt")},
                }
                (state.pending if state.mode == "delayed" else state.posts).append(result)
                if state.mode == "duplicates":
                    state.posts.append({**result, "id": result["id"] + 1})
                # A truncated successful response is ambiguous even when insertion happened.
                if state.mode in {"lost", "delayed", "duplicates"}:
                    self.reply(201, None, truncated=True)
                elif state.mode == "postwrite_error":
                    self.reply(400, {"code": "rest_meta_database_error"})
                else:
                    self.reply(201, result)

            def reply(self, status, value, truncated=False):
                body = b"{" if truncated else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, outbound, *, policy):
        started = time.monotonic()
        with closing(http.client.HTTPConnection(*self.server.server_address, timeout=2)) as client:
            path = urlsplit(outbound.url)
            client.request(
                outbound.method,
                path.path + ("?" + path.query if path.query else ""),
                body=outbound.body,
                headers=dict(outbound.headers),
            )
            response = client.getresponse()
            body = response.read(131073)
            status = response.status
            media_type = response.getheader("Content-Type")
        time.sleep(0.005)
        return EgressHttpResult(
            1,
            outbound.url,
            outbound.url,
            outbound.method,
            "fetched",
            status,
            media_type,
            (("content-type", media_type),),
            self.resolved_address,
            body,
            hashlib.sha256(body).hexdigest(),
            len(body),
            max(5, int((time.monotonic() - started) * 1000)),
        )


@pytest.fixture
async def wordpress_harness(
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
    monkeypatch,
):
    scope, context = scopes[0], identity_context
    origin = _verify_origin(admin, identity, context, scope)
    common = dict(
        session_token=context["session_token"],
        generation=context["generation"],
        site_id=scope.site_id,
    )
    store = EncryptedLocalArtifactStore(tmp_path / "wp-objects")
    run, policy, snapshot = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "wordpress-" + uuid4().hex,
        store,
        origin_override=origin,
        github_profile=True,
    )
    double = CoreDouble()
    double.origin = origin
    double.resolved_address = snapshot.resolved_address
    gateway = SharedEgressProvider(
        admission_connection=crawl_admission,
        ingest_connection=crawl_ingest,
        store=store,
        run=run,
        policy=policy,
        fetcher=double,
        worker_key="worker.wordpress",
        admission_policy=OriginAdmissionPolicy(),
        artifact_key=None,
        purpose="connector",
    )

    async def credential(self, binding_id, **options):
        return WordPressCredential("synthetic-author", "synthetic-application-password")

    monkeypatch.setattr(OpenBaoWordPressSecrets, "credential", credential)
    with psycopg.connect(
        os.environ["SIGNAL_TEST_WRITE_JOURNAL_DSN"], autocommit=True
    ) as journal_connection:
        journal = WriteIntentJournal(journal_connection, _KEY.public_key(), _ENCRYPTION, _KEY)
        service = WordPressService(
            OpenBaoWordPressSecrets("https://bao.example.invalid", "synthetic-bao-reader"),
            journal,
            lambda site, target: gateway,
        )
        binding_id = uuid4()
        assert (await service.connect(api, **common, binding_id=binding_id, origin=origin))[
            "state"
        ] == "bound"
        propose_fact(
            api,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
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
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            fact_id=fid,
        )
        draft_id, brief_id = uuid4(), uuid4()
        admin.execute(
            "INSERT INTO app.content_briefs(tenant_id,site_id,id,payload,origin,created_by) "
            "VALUES(%s,%s,%s,%s,'owner',%s)",
            (
                scope.tenant_id,
                scope.site_id,
                brief_id,
                Jsonb(
                    {
                        "kind": "new_article",
                        "fact_ids": [str(fid)],
                        "source_ids": [],
                        "internal_links": [],
                    }
                ),
                context["user_id"],
            ),
        )
        admin.execute(
            "INSERT INTO app.content_brief_acceptances(tenant_id,site_id,brief_id,actor_id) "
            "VALUES(%s,%s,%s,%s)",
            (scope.tenant_id, scope.site_id, brief_id, context["user_id"]),
        )
        admin.execute(
            "INSERT INTO app.content_draft_intents(tenant_id,site_id,id,brief_id,writer_version,"
            "input_sha256,fact_snapshot) VALUES(%s,%s,%s,%s,'content-writer-v1',%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                draft_id,
                brief_id,
                bytes(32),
                Jsonb(
                    [
                        {
                            "fact_id": str(fid),
                            "category": "audience",
                            "statement": "Founders are our audience.",
                        }
                    ]
                ),
            ),
        )
        result = {
            "state": "grounded",
            "article": article(fid),
            "originality": {"state": "original"},
        }
        canonical = rfc8785.dumps(result)
        admin.execute(
            "INSERT INTO app.content_draft_results(tenant_id,site_id,draft_id,payload,canonical,"
            "sha256) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                draft_id,
                Jsonb(result),
                canonical,
                hashlib.sha256(canonical).digest(),
            ),
        )
        sealed = service.seal(api, **common, binding_id=binding_id, draft_id=draft_id)
        candidate_id = UUID(sealed["candidate_id"])
        projection = service.read(api, **common)
        digest = projection["candidates"][0]["revision_sha256"]
        h = SimpleNamespace(
            service=service,
            gateway=gateway,
            double=double,
            scope=scope,
            common=common,
            context=context,
            candidate_id=candidate_id,
            binding_id=binding_id,
            digest=digest,
            journal=journal,
            draft_id=draft_id,
            brief_id=brief_id,
            fid=fid,
        )

        def live_factory(site_id, url):
            live_run, live_policy, _ = authority(
                api,
                scheduler,
                workflow,
                crawl_admission,
                crawl_ingest,
                scope,
                "wp-live-" + uuid4().hex,
                store,
                origin_override=origin,
                verification_profile=True,
                seed_url=url,
            )
            return SharedLiveVerifier(
                crawl_admission,
                crawl_ingest,
                store,
                live_run,
                live_policy,
                double,
                "worker.wordpress-live",
                OriginAdmissionPolicy(),
            )

        h.live_factory = live_factory

        def ready():
            delay = admin.execute(
                "SELECT greatest(0,extract(epoch FROM max(greatest(next_allowed_at,degraded_until))"
                "-clock_timestamp())) FROM control.origin_buckets WHERE origin=%s",
                (origin,),
            ).fetchone()[0]
            if delay:
                time.sleep(float(delay) + 0.02)

        h.ready = ready
        h.ready()
        try:
            yield h
        finally:
            double.close()


def approve(h, api):
    assert (
        h.service.review(
            api,
            **h.common,
            candidate_id=h.candidate_id,
            revision_sha256=h.digest,
            decision="approved",
        )["state"]
        == "reviewed"
    )


def permit_read(admin, intent_id):
    admin.execute(
        "UPDATE app.wordpress_intents SET next_read_at=clock_timestamp() WHERE id=%s", (intent_id,)
    )


@pytest.mark.anyio
async def test_owner_approval_exact_revision_and_idempotent_draft(
    wordpress_harness, admin, api, identity
):
    h = wordpress_harness
    projection = h.service.read(api, **h.common)
    assert any(d["draft_id"] == str(h.draft_id) for d in projection["drafts"])
    assert all(set(d) == {"draft_id", "title"} for d in projection["drafts"])
    intent_id = uuid4()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "approval_required"
    assert (
        h.service.review(
            api,
            **h.common,
            candidate_id=h.candidate_id,
            revision_sha256="f" * 64,
            decision="approved",
        )["state"]
        == "stale"
    )
    approve(h, api)
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "recorded"
    for _ in range(2):
        assert (
            await h.service.create(
                identity, **h.common, candidate_id=h.candidate_id, intent_id=uuid4()
            )
        )["state"] == "recorded"
    assert len(h.double.posts) == 1
    _, entries = h.journal.verify_stream()
    assert any(getattr(r, "intent_id", None) == intent_id for r, _ in entries)
    calls = [c for c in h.double.calls if c[0] == "POST"]
    assert (
        len(calls) == 1
        and calls[0][1] == "/wp-json/wp/v2/posts"
        and calls[0][2]["status"] == "draft"
    )
    assert "synthetic-application-password" not in json.dumps(h.service.read(api, **h.common))
    assert "synthetic-application-password" not in str(
        admin.execute(
            "SELECT request_url,request_sha256 FROM app.egress_operations WHERE site_id=%s",
            (h.scope.site_id,),
        ).fetchall()
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("lost", "recorded"),
        ("delayed", "outcome_unknown"),
        ("duplicates", "escalated"),
        ("postwrite_error", "recorded"),
    ],
)
async def test_ambiguous_outcomes_never_resend_and_delayed_commit(
    wordpress_harness, admin, api, identity, mode, expected
):
    h = wordpress_harness
    approve(h, api)
    h.double.mode = mode
    intent_id = uuid4()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "outcome_unknown"
    h.ready()
    assert (await h.service.reconcile(identity, **h.common, intent_id=intent_id))[
        "state"
    ] == expected
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == expected
    assert len([c for c in h.double.calls if c[0] == "POST"]) == 1
    if mode == "delayed":
        assert (await h.service.reconcile(identity, **h.common, intent_id=intent_id))[
            "state"
        ] == "read_deferred"
        h.double.posts.extend(h.double.pending)
        h.double.pending.clear()
        permit_read(admin, intent_id)
        h.ready()
        assert (await h.service.reconcile(identity, **h.common, intent_id=intent_id))[
            "state"
        ] == "recorded"
        assert len([c for c in h.double.calls if c[0] == "POST"]) == 1


@pytest.mark.anyio
async def test_definitive_rejection_retries_once_and_then_closes(wordpress_harness, api, identity):
    h = wordpress_harness
    approve(h, api)
    h.double.mode = "rejected"
    intent_id = uuid4()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "retry"
    h.ready()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "failed"
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "failed"
    assert not h.double.posts and len([c for c in h.double.calls if c[0] == "POST"]) == 2


@pytest.mark.anyio
async def test_pre_dispatch_deferral_and_retry_no_duplicate(
    wordpress_harness, admin, api, identity, monkeypatch
):
    h = wordpress_harness
    approve(h, api)
    original = SharedEgressProvider.request_json

    def deferred(gateway, **values):
        if values["method"] == "POST":
            admin.execute(
                "UPDATE control.origin_buckets SET next_allowed_at=clock_timestamp()+interval "
                "'2 seconds' WHERE origin=%s",
                (h.double.origin,),
            )
        return original(gateway, **values)

    monkeypatch.setattr(SharedEgressProvider, "request_json", deferred)
    intent_id = uuid4()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "retry"
    assert not h.double.posts and not [c for c in h.double.calls if c[0] == "POST"]
    monkeypatch.setattr(SharedEgressProvider, "request_json", original)
    h.ready()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "recorded"
    assert len(h.double.posts) == 1


@pytest.mark.anyio
async def test_fresh_owner_intent_keeps_old_quarantined_and_cap(
    wordpress_harness, admin, api, identity
):
    h = wordpress_harness
    approve(h, api)
    h.double.mode = "delayed"
    old = uuid4()
    assert (
        await h.service.create(identity, **h.common, candidate_id=h.candidate_id, intent_id=old)
    )["state"] == "outcome_unknown"
    h.ready()
    h.double.mode = "normal"
    new = uuid4()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=new, prior_intent_id=old
        )
    )["state"] == "recorded"
    assert h.double.pending[0]["slug"] != h.double.posts[0]["slug"]
    assert (
        await h.service.create(
            identity,
            **h.common,
            candidate_id=h.candidate_id,
            intent_id=uuid4(),
            prior_intent_id=old,
        )
    )["state"] == "cap_reached"
    assert (
        admin.execute("SELECT state FROM app.wordpress_intents WHERE id=%s", (old,)).fetchone()[0]
        == "outcome_unknown"
    )
    h.double.posts.extend(h.double.pending)
    h.ready()
    assert (await h.service.reconcile(identity, **h.common, intent_id=old))["state"] == "recorded"
    assert len(h.double.posts) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        "role",
        "tenant",
        "site",
        "generation",
        "deprovision",
        "caps",
        "superseded",
        "pause",
        "fact_removed",
        "membership_epoch",
        "site_epoch",
        "origin_generation",
    ],
)
async def test_authority_and_binding_negatives(
    wordpress_harness, admin, api, identity, scopes, change
):
    h = wordpress_harness
    approve(h, api)
    common = dict(h.common)
    if change == "role":
        admin.execute(
            "UPDATE app.memberships SET role_key='analyst' WHERE id=%s",
            (h.context["membership_id"],),
        )
    elif change == "tenant":
        common["site_id"] = scopes[2].site_id
    elif change == "site":
        common["site_id"] = scopes[1].site_id
    elif change == "generation":
        common["generation"] = "different-generation"
    elif change == "membership_epoch":
        admin.execute(
            "UPDATE app.memberships SET authorization_epoch=3 WHERE id=%s",
            (h.context["membership_id"],),
        )
    elif change == "site_epoch":
        admin.execute(
            "UPDATE app.site_memberships SET authorization_epoch=2 WHERE id=%s",
            (h.context["site_membership_id"],),
        )
    elif change == "origin_generation":
        admin.execute(
            "UPDATE control.public_origin_claims SET claim_generation=claim_generation+1 "
            "WHERE site_id=%s",
            (h.scope.site_id,),
        )
    elif change == "deprovision":
        admin.execute(
            "UPDATE app.memberships SET state='removed' WHERE id=%s", (h.context["membership_id"],)
        )
    elif change == "caps":
        h.double.user["capabilities"]["manage_options"] = True
    elif change == "superseded":
        admin.execute(
            "INSERT INTO app.content_briefs(tenant_id,site_id,id,payload,origin,created_by,"
            "supersedes_id) VALUES(%s,%s,%s,'{}','owner',%s,%s)",
            (h.scope.tenant_id, h.scope.site_id, uuid4(), h.context["user_id"], h.brief_id),
        )
    elif change == "pause":
        admin.execute(
            "INSERT INTO app.site_weekly_control(tenant_id,site_id,paused) VALUES(%s,%s,true)",
            (h.scope.tenant_id, h.scope.site_id),
        )
    elif change == "fact_removed":
        from signal_core.business_brain import remove_fact

        remove_fact(
            api,
            session_token=h.common["session_token"],
            current_recovery_generation=h.common["generation"],
            site_id=h.scope.site_id,
            fact_id=h.fid,
        )
    try:
        result = await h.service.create(
            identity, **common, candidate_id=h.candidate_id, intent_id=uuid4()
        )
        assert result["state"] in {"stale", "approval_required"}
    except WordPressUnavailable:
        pass
    assert not h.double.posts


@pytest.mark.anyio
async def test_binding_origin_capabilities_revocation_and_unconfigured(
    wordpress_harness, admin, api, identity
):
    h = wordpress_harness
    with pytest.raises(WordPressUnavailable):
        await h.service.connect(
            api, **h.common, binding_id=uuid4(), origin="https://other.example.invalid"
        )
    assert replace(h.service, secrets=None).read(api, **h.common)["provider_state"] == "unavailable"
    result = h.service.revoke(api, **h.common, binding_id=h.binding_id)
    assert result["state"] == "AUTHORITY_DURABILITY_PENDING"
    assert admin.execute(
        "SELECT target_kind FROM control.authority_restriction_outbox WHERE target_id=%s",
        (h.binding_id,),
    ).fetchone() == ("wordpress_binding",)
    assert h.service.read(api, **h.common)["bindings"][0]["current"] is False
    assert (
        await h.service.create(identity, **h.common, candidate_id=h.candidate_id, intent_id=uuid4())
    )["state"] == "approval_required"


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["content", "slug"])
async def test_owner_publish_observation_is_read_only_and_edited_not_failure(
    wordpress_harness, admin, api, identity, field
):
    h = wordpress_harness
    approve(h, api)
    intent_id = uuid4()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "recorded"
    h.double.posts[0]["status"] = "publish"
    h.ready()
    assert (await h.service.observe(identity, **h.common, intent_id=intent_id))[
        "state"
    ] == "published_unverified"
    if field == "content":
        h.double.posts[0]["content"]["raw"] = "Owner edited the draft."
    else:
        h.double.posts[0]["slug"] = "owner-selected-permalink"
    permit_read(admin, intent_id)
    h.ready()
    assert (await h.service.observe(identity, **h.common, intent_id=intent_id))[
        "state"
    ] == "edited_before_publish"
    assert len([c for c in h.double.calls if c[0] == "POST"]) == 1


@pytest.mark.anyio
async def test_journal_unavailable_and_runtime_cannot_bypass_fence(
    wordpress_harness, api, identity
):
    h = wordpress_harness
    approve(h, api)
    with pytest.raises(WordPressUnavailable):
        await replace(h.service, journal=None).create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=uuid4()
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        wordpress_call(api, *h.common.values(), "claim", uuid4(), Jsonb({}), bytes(32))
    for table in [
        "wordpress_bindings",
        "wordpress_candidates",
        "wordpress_reviews",
        "wordpress_intents",
        "wordpress_receipts",
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(f"DELETE FROM app.{table}")
    assert not h.double.posts


@pytest.mark.anyio
@pytest.mark.parametrize("attempt", [1, 2])
async def test_independent_attempt_receipt_blocks_crash_or_restored_primary_resend(
    wordpress_harness, api, identity, admin, attempt
):
    h = wordpress_harness
    approve(h, api)
    intent_id = uuid4()
    wordpress_call(identity, *h.common.values(), "queue", h.candidate_id, intent_id, None)
    packet = wordpress_call(identity, *h.common.values(), "packet", intent_id)
    message = rfc8785.dumps({**packet["candidate"]["payload"], "slug": packet["intent"]["marker"]})
    for number in range(1, attempt + 1):
        h.journal.append(
            WordPressWriteIntentRecord(
                uuid5(NAMESPACE_URL, f"signal.wordpress.dispatch:{intent_id}:{number}"),
                intent_id,
                number,
                h.scope.site_id,
                h.binding_id,
                h.digest,
                hashlib.sha256(message).hexdigest(),
                packet["intent"]["marker"],
                h.common["generation"],
            )
        )
    if attempt == 2:
        admin.execute(
            "UPDATE app.wordpress_intents SET state='retry',attempts=1 WHERE id=%s", (intent_id,)
        )
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "outcome_unknown"
    assert not [c for c in h.double.calls if c[0] == "POST"]


@pytest.mark.anyio
async def test_reconciliation_budget_exhausted_no_post(wordpress_harness, admin, api, identity):
    h = wordpress_harness
    approve(h, api)
    h.double.mode = "delayed"
    intent_id = uuid4()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "outcome_unknown"
    admin.execute("UPDATE app.wordpress_intents SET reconcile_count=12 WHERE id=%s", (intent_id,))
    calls = len(h.double.calls)
    assert (await h.service.reconcile(identity, **h.common, intent_id=intent_id))[
        "state"
    ] == "read_exhausted"
    assert len(h.double.calls) == calls


@pytest.mark.anyio
async def test_public_get_verification_uses_sealed_article_without_credentials(
    wordpress_harness, api, identity
):
    h = wordpress_harness
    approve(h, api)
    intent_id = uuid4()
    assert (
        await h.service.create(
            identity, **h.common, candidate_id=h.candidate_id, intent_id=intent_id
        )
    )["state"] == "recorded"
    h.double.posts[0]["status"] = "publish"
    h.ready()
    live_factory = h.live_factory

    def delayed_live(site_id, url):
        h.ready()
        return live_factory(site_id, url)

    service = replace(h.service, live_factory=delayed_live)
    assert (await service.observe(identity, **h.common, intent_id=intent_id))["state"] == "verified"
    assert len([c for c in h.double.calls if c[0] == "POST"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["administrator", "editor", "author"])
async def test_binding_refuses_actual_role_or_excessive_capabilities(wordpress_harness, api, role):
    h = wordpress_harness
    h.double.user["roles"] = [role]
    h.double.user["capabilities"]["manage_options"] = True
    with pytest.raises(WordPressUnavailable, match="WORDPRESS_EXCESSIVE_CAPABILITIES"):
        await h.service.connect(api, **h.common, binding_id=uuid4(), origin=h.double.origin)
    assert not h.double.posts


@pytest.mark.anyio
async def test_egress_rechecks_pause_after_journal_claim(
    wordpress_harness, admin, api, identity, monkeypatch
):
    h = wordpress_harness
    approve(h, api)
    original = SharedEgressProvider.request_json

    def pause_before_io(gateway, **values):
        if values["method"] == "POST":
            admin.execute(
                "INSERT INTO app.site_weekly_control(tenant_id,site_id,paused) VALUES(%s,%s,true)",
                (h.scope.tenant_id, h.scope.site_id),
            )
        return original(gateway, **values)

    monkeypatch.setattr(SharedEgressProvider, "request_json", pause_before_io)
    assert (
        await h.service.create(identity, **h.common, candidate_id=h.candidate_id, intent_id=uuid4())
    )["state"] == "outcome_unknown"
    assert not [c for c in h.double.calls if c[0] == "POST"]


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["tenant_id", "site_id", "origin"])
async def test_operator_credential_assignment_is_checked_before_any_provider_request(
    wordpress_harness, api, identity, monkeypatch, field
):
    h = wordpress_harness
    assignment = {
        "tenant_id": str(h.scope.tenant_id),
        "site_id": str(h.scope.site_id),
        "origin": h.double.origin,
    }
    assignment[field] = "https://other.example.invalid" if field == "origin" else str(uuid4())

    async def secret_response(**kwargs):
        return httpx2.Response(
            200,
            json={
                "data": {
                    "metadata": {"destroyed": False, "deletion_time": "", "version": 1},
                    "data": {
                        **assignment,
                        "username": "synthetic-author",
                        "password": "synthetic-application-password",
                    },
                }
            },
        )

    monkeypatch.setattr("signal_core.wordpress_secrets.request", secret_response)
    monkeypatch.setattr(OpenBaoWordPressSecrets, "credential", _CREDENTIAL_READ)
    calls = len(h.double.calls)
    with pytest.raises(WordPressUnavailable, match="^WORDPRESS_CREDENTIAL_UNAVAILABLE$"):
        await h.service.connect(api, **h.common, binding_id=uuid4(), origin=h.double.origin)
    approve(h, api)
    with pytest.raises(WordPressUnavailable, match="^WORDPRESS_CREDENTIAL_UNAVAILABLE$"):
        await h.service.create(identity, **h.common, candidate_id=h.candidate_id, intent_id=uuid4())
    assert len(h.double.calls) == calls
    assert not h.double.posts
