"""Owner-approved Core REST drafts, with durable intent-before-I/O fencing."""

import asyncio
import hashlib
from dataclasses import asdict, dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import rfc8785
from psycopg.types.json import Jsonb

from signal_core.business_brain import _session
from signal_core.connector_framework import connector_row
from signal_core.content_writer_service import writer_call
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import WordPressScope
from signal_core.live_verification import LivePostcondition, SharedLiveVerifier
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.wordpress_protocol import (
    PUBLISH_UNAVAILABLE,
    QUARANTINE_REASON,
    UPDATES_UNAVAILABLE,
    WordPressUnavailable,
    content_digest,
    least_privilege_user,
    marker,
    post,
    public_url,
    render_draft,
    rest,
)
from signal_core.wordpress_secrets import OpenBaoWordPressSecrets
from signal_core.write_intent_journal import WordPressWriteIntentRecord, WriteIntentJournal


def wordpress_call(connection, session_token, generation, site_id, name, *args):
    if name not in {
        "origin",
        "secret_scope",
        "bind",
        "read",
        "seal",
        "review",
        "queue",
        "packet",
        "claim",
        "finish",
        "read_claim",
        "observation",
        "revoke",
    }:
        raise ValueError("Invalid WordPress port.")
    token_hash, generation = _session(session_token, generation)
    result = connector_row(
        connection,
        f"wordpress_{name}",
        token_hash,
        generation,
        site_id,
        *args,
        scalar=True,
        transaction=_clean_transaction,
    )
    if (
        result is None
        or result == "denied"
        or isinstance(result, dict)
        and result.get("state") == "denied"
    ):
        raise WordPressUnavailable("owner_access_denied")
    return result


@dataclass(frozen=True, repr=False)
class WordPressService:
    secrets: OpenBaoWordPressSecrets | None = field(default=None, repr=False)
    journal: WriteIntentJournal | None = field(default=None, repr=False)
    egress_factory: object | None = field(default=None, repr=False)
    live_factory: object | None = field(default=None, repr=False)
    secret_options: dict = field(default_factory=dict, repr=False)

    @property
    def configured(self):
        return (
            isinstance(self.secrets, OpenBaoWordPressSecrets)
            and isinstance(self.journal, WriteIntentJournal)
            and callable(self.egress_factory)
        )

    def read(self, connection, **common):
        result = wordpress_call(connection, **common, name="read")
        token_hash, generation = _session(common["session_token"], common["generation"])
        with _clean_transaction(connection):
            options = connection.execute(
                "SELECT control.owner_publishing_options(%s,%s,%s)",
                (token_hash, generation, common["site_id"]),
            ).fetchone()[0]
        return {
            **result,
            "drafts": options["drafts"] if options is not None else [],
            "provider_state": "available" if self.configured else "unavailable",
            "provider_reason": None if self.configured else "WORDPRESS_UNCONFIGURED",
            "updates_reason": UPDATES_UNAVAILABLE,
            "publish_reason": PUBLISH_UNAVAILABLE,
            "quarantine_reason": QUARANTINE_REASON,
        }

    def _egress(self, site_id, origin, tenant_id=None):
        if not self.configured:
            raise WordPressUnavailable("WORDPRESS_UNCONFIGURED")
        gateway = self.egress_factory(site_id, origin)
        if (
            not isinstance(gateway, SharedEgressProvider)
            or gateway.purpose != "connector"
            or gateway.run.site_id != site_id
            or tenant_id is not None
            and gateway.run.tenant_id != UUID(tenant_id)
            or gateway.policy.allowed_origins != (origin,)
            or gateway.policy.max_redirects != 0
        ):
            raise WordPressUnavailable("WORDPRESS_EGRESS_SCOPE_REJECTED")
        return gateway

    async def connect(self, connection, *, binding_id, origin, **common):
        if not wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "origin",
            origin,
        ):
            raise WordPressUnavailable("WORDPRESS_ORIGIN_UNVERIFIED")
        assignment = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "secret_scope",
            origin,
        )
        gateway = self._egress(common["site_id"], origin, assignment["tenant_id"])
        credential = await self.secrets.credential(
            binding_id,
            tenant_id=UUID(assignment["tenant_id"]),
            site_id=common["site_id"],
            origin=origin,
            **self.secret_options,
        )
        status, value = rest(
            gateway,
            WordPressScope(origin, credential.authorization),
            path="users/me?context=edit",
            operation_id=uuid4(),
        )
        if status != 200:
            raise WordPressUnavailable("WORDPRESS_IDENTITY_UNAVAILABLE")
        observed = least_privilege_user(value)
        result = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "bind",
            binding_id,
            origin,
            observed["id"],
            Jsonb(observed),
        )
        return {"state": result}

    def seal(self, connection, *, binding_id, draft_id, **common):
        projection = writer_call(connection, **common, name="read")
        draft = next((d for d in projection["drafts"] if d["draft_id"] == str(draft_id)), None)
        if draft is None or draft["result"].get("state") not in {"grounded", "owner_required"}:
            raise WordPressUnavailable("WORDPRESS_DRAFT_UNAVAILABLE")
        document = render_draft(draft["result"]["article"])
        canonical = rfc8785.dumps(document)
        return wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "seal",
            uuid4(),
            binding_id,
            draft_id,
            canonical,
            hashlib.sha256(canonical).digest(),
        )

    def review(self, connection, *, candidate_id, revision_sha256, decision, **common):
        return {
            "state": wordpress_call(
                connection,
                common["session_token"],
                common["generation"],
                common["site_id"],
                "review",
                candidate_id,
                bytes.fromhex(revision_sha256),
                decision,
            )
        }

    async def _provider(self, packet, site_id):
        binding = packet["binding"]
        gateway = self._egress(site_id, binding["origin"], binding["tenant_id"])
        credential = await self.secrets.credential(
            UUID(binding["id"]),
            tenant_id=UUID(binding["tenant_id"]),
            site_id=site_id,
            origin=binding["origin"],
            **self.secret_options,
        )
        scope = WordPressScope(
            binding["origin"],
            credential.authorization,
            binding["user_id"],
            packet["intent"]["marker"],
        )
        status, value = rest(gateway, scope, path="users/me?context=edit", operation_id=uuid4())
        if status != 200 or least_privilege_user(value) != binding["observed"]:
            raise WordPressUnavailable("WORDPRESS_BINDING_STALE")
        await asyncio.sleep(gateway.admission_policy.min_delay_ms / 1000 + 0.01)
        return gateway, scope

    async def create(self, connection, *, candidate_id, intent_id, prior_intent_id=None, **common):
        queued = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "queue",
            candidate_id,
            intent_id,
            prior_intent_id,
        )
        if "intent_id" not in queued:
            return queued
        intent_id = UUID(queued["intent_id"])
        packet = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "packet",
            intent_id,
        )
        if packet["intent"]["state"] not in {"queued", "retry"}:
            return {"state": packet["intent"]["state"], "intent_id": str(intent_id)}
        gateway, scope = await self._provider(packet, common["site_id"])
        document = {**packet["candidate"]["payload"], "slug": marker(intent_id)}
        message = hashlib.sha256(rfc8785.dumps(document)).digest()
        record = WordPressWriteIntentRecord(
            uuid5(
                NAMESPACE_URL,
                f"signal.wordpress.dispatch:{intent_id}:{packet['intent']['attempts'] + 1}",
            ),
            intent_id,
            packet["intent"]["attempts"] + 1,
            common["site_id"],
            UUID(packet["binding"]["id"]),
            packet["candidate"]["revision_sha256"][2:],
            message.hex(),
            scope.slug,
            common["generation"],
        )
        # Independent acknowledgement precedes both the dispatch fence and provider I/O.
        _, entries = self.journal.verify_stream()
        already_journalled = any(r.operation_id == record.operation_id for r, _ in entries)
        receipt = asdict(self.journal.append(record))
        receipt["operation_id"] = receipt.pop("event_id")
        receipt["write_intent_id"] = intent_id
        receipt["attempt"] = record.attempt
        receipt = {
            k: str(v) if isinstance(v, UUID) else v.isoformat() if hasattr(v, "isoformat") else v
            for k, v in receipt.items()
        }
        claim = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "claim",
            intent_id,
            Jsonb(receipt),
            message,
        )
        if claim != "claimed":
            return {"state": claim, "intent_id": str(intent_id)}
        if already_journalled:
            return self._finish(connection, common, intent_id, "outcome_unknown")
        outcome, observed = "outcome_unknown", None
        try:
            status, value = rest(
                gateway, scope, path="posts", operation_id=uuid4(), document=document
            )
            if status == 201:
                observed = post(value, author_id=scope.author_id, slug=scope.slug)
                if observed["status"] != "draft" or observed["content_sha256"] != content_digest(
                    document
                ):
                    raise WordPressUnavailable("WORDPRESS_RESPONSE_INVALID")
                public_url(observed["url"], scope.origin, observed["post_id"])
                outcome = "recorded"
            elif isinstance(value, dict) and (status, value.get("code")) in {
                (400, "rest_invalid_param"),
                (400, "rest_invalid_json"),
                (401, "rest_cannot_create"),
                (403, "rest_cannot_create"),
                (401, "rest_not_logged_in"),
            }:
                outcome = "unapplied_rejection"
        except ProviderEgressUnavailable as error:
            # This code means admission stopped the request before provider dispatch.
            if error.code == "EGRESS_DEFERRED":
                outcome = "not_transmitted"
        except Exception:
            # Neither a timeout nor a generic provider error proves non-transmission.
            pass
        return self._finish(connection, common, intent_id, outcome, observed)

    def _finish(self, connection, common, intent_id, outcome, observed=None):
        state = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "finish",
            intent_id,
            outcome,
            observed["post_id"] if observed else None,
            observed["url"] if observed else None,
            observed["content_sha256"] if observed else None,
        )
        return {"state": state, "intent_id": str(intent_id)}

    async def reconcile(self, connection, *, intent_id, **common):
        read_state = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "read_claim",
            intent_id,
        )
        if read_state != "read_claimed":
            return {"state": read_state}
        packet = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "packet",
            intent_id,
        )
        if packet["intent"]["state"] == "recorded":
            return {"state": "recorded", "intent_id": str(intent_id)}
        gateway, scope = await self._provider(packet, common["site_id"])
        outcome, observed = "outcome_unknown", None
        try:
            status, value = rest(
                gateway,
                scope,
                path=f"posts?slug={scope.slug}&status=draft&context=edit&author={scope.author_id}&per_page=100",
                operation_id=uuid4(),
            )
            if status == 200 and isinstance(value, list):
                matches = [post(v, author_id=scope.author_id, slug=scope.slug) for v in value]
                if any(v["status"] != "draft" for v in matches):
                    raise WordPressUnavailable("WORDPRESS_RESPONSE_INVALID")
                if len(matches) == 1:
                    observed = matches[0]
                    public_url(observed["url"], scope.origin, observed["post_id"])
                    outcome = "recorded"
                elif len(matches) > 1:
                    outcome = "escalated"
        except Exception:
            pass
        return self._finish(connection, common, intent_id, outcome, observed)

    async def observe(self, connection, *, intent_id, **common):
        read_state = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "read_claim",
            intent_id,
        )
        if read_state != "read_claimed":
            return {"state": read_state}
        packet = wordpress_call(
            connection,
            common["session_token"],
            common["generation"],
            common["site_id"],
            "packet",
            intent_id,
        )
        intent = packet["intent"]
        if intent["state"] != "recorded":
            return {"state": "not_recorded"}
        gateway, scope = await self._provider(packet, common["site_id"])
        scope = WordPressScope(
            scope.origin, scope.authorization, scope.author_id, scope.slug, intent["post_id"]
        )
        outcome, digest = "observation_unavailable", None
        try:
            status, value = rest(
                gateway, scope, path=f"posts/{scope.post_id}?context=edit", operation_id=uuid4()
            )
            if status == 200:
                observed = post(
                    value, author_id=scope.author_id, slug=scope.slug, post_id=scope.post_id
                )
                digest = observed["content_sha256"]
                if observed["status"] == "publish":
                    if observed["slug_changed"] or digest != content_digest(
                        packet["candidate"]["payload"]
                    ):
                        outcome = "edited_before_publish"
                    else:
                        outcome = "published_unverified"
                        url = public_url(observed["url"], scope.origin, observed["post_id"])
                        if callable(self.live_factory):
                            await asyncio.sleep(gateway.admission_policy.min_delay_ms / 1000 + 0.01)
                            verifier = self.live_factory(common["site_id"], url)
                            if not isinstance(verifier, SharedLiveVerifier):
                                raise WordPressUnavailable("WORDPRESS_LIVE_UNAVAILABLE")
                            result, _ = verifier.verify(
                                LivePostcondition(
                                    "wordpress_article",
                                    url,
                                    "0" * 64,
                                    packet["candidate"]["revision_sha256"][2:],
                                    {"article_fragment": packet["candidate"]["payload"]["content"]},
                                    "Owner edits in WordPress; Signal cannot change or delete "
                                    "this post.",
                                ),
                                site_id=common["site_id"],
                                operation_id=uuid4(),
                            )
                            if result.outcome == "verified":
                                outcome = "verified"
        except Exception:
            pass
        return {
            "state": wordpress_call(
                connection,
                common["session_token"],
                common["generation"],
                common["site_id"],
                "observation",
                intent_id,
                outcome,
                digest,
            )
        }

    def revoke(self, connection, *, binding_id, **common):
        return {
            "state": wordpress_call(
                connection,
                common["session_token"],
                common["generation"],
                common["site_id"],
                "revoke",
                binding_id,
                uuid4(),
            )
        }
