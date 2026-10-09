"""Owner-confirmed mapping, exact Inbox approval, and one-shot draft delivery."""

import hashlib
import os
import secrets
from dataclasses import dataclass, field, replace
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from psycopg import Connection, Error
from psycopg.types.json import Jsonb

from signal_core.connector_framework import connector_row
from signal_core.crawl_http import CrawlFetchRejected
from signal_core.database import _clean_transaction
from signal_core.decision_contracts import canonical_json
from signal_core.egress_profiles import WebflowScope
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token
from signal_core.webflow import (
    CAPABILITIES,
    SCOPES,
    WebflowUnavailable,
    draft_body,
    object_id,
    published_domain,
    reconcile_items,
    schema_digest,
    secret,
    validate_grant,
    validate_mapping,
)
from signal_core.webflow_provider import WebflowProvider, oauth_request
from signal_core.webflow_secrets import OpenBaoWebflowSecrets
from signal_core.write_intent_journal import (
    WebflowWriteIntentRecord,
    WriteIntentJournal,
    WriteIntentJournalConflict,
    WriteIntentJournalUnavailable,
)

_ACTIONS = frozenset(
    {
        "read",
        "options",
        "preflight",
        "begin_oauth",
        "consume_oauth",
        "bind",
        "binding",
        "source",
        "seal",
        "review",
        "packet",
        "eligibility",
        "authorize",
        "send",
        "quarantine",
        "reconcile",
        "revoke",
    }
)


def webflow_call(connection, session_token, generation, site_id, action, payload=None):
    if action not in _ACTIONS:
        raise WebflowUnavailable(CAPABILITIES.get(action, "WEBFLOW_ACTION_UNAVAILABLE"))
    try:
        digest = hash_session_token(session_token)
        generation = validate_recovery_generation(generation)
        result = connector_row(
            connection,
            "webflow_command",
            digest,
            generation,
            site_id,
            action,
            Jsonb(payload or {}),
            transaction=_clean_transaction,
        )
        if result is None or result[0] is None:
            raise WebflowUnavailable("WEBFLOW_OWNER_ACCESS_OR_SOURCE_UNAVAILABLE")
        if result[0] == {"state": "step_up_required"}:
            raise WebflowUnavailable("WEBFLOW_STEP_UP_REQUIRED")
        return result[0]
    except (Error, InvalidOpaqueSessionToken, TypeError, ValueError):
        raise WebflowUnavailable("WEBFLOW_OWNER_ACCESS_OR_SOURCE_UNAVAILABLE") from None


class _OneShotFetcher:
    def __init__(self, fetcher, service, session_token, site_id, operation_id, nonce, digest):
        self.fetcher, self.service, self.session_token = fetcher, service, session_token
        self.site_id, self.operation_id, self.nonce, self.digest = (
            site_id,
            operation_id,
            nonce,
            digest,
        )
        self.used = False

    def request(self, request, *, policy):
        if self.used:
            raise CrawlFetchRejected("Webflow draft permit already consumed.")
        self.used = True
        # Current authority locks cover the last possible outbound byte, not just planning.
        with _clean_transaction(self.service.connection):
            result = self.service.connection.execute(
                "SELECT control.webflow_command(%s,%s,%s,'send',%s)",
                (
                    hash_session_token(self.session_token),
                    self.service.recovery_generation,
                    self.site_id,
                    Jsonb(
                        {
                            "id": str(self.operation_id),
                            "nonce": str(self.nonce),
                            "revision_sha256": self.digest,
                        }
                    ),
                ),
            ).fetchone()
            if result != ({"state": "send_once"},):
                raise CrawlFetchRejected("Webflow draft permit denied.")
            return self.fetcher.request(request, policy=policy)


@dataclass(frozen=True, repr=False)
class WebflowService:
    connection: Connection = field(repr=False)
    secrets_store: OpenBaoWebflowSecrets = field(repr=False)
    dashboard_origin: str
    recovery_generation: str
    write_journal: WriteIntentJournal | None = field(default=None, repr=False)
    openbao_options: dict = field(default_factory=dict, repr=False)
    disposable_test_writes: bool = False

    def __post_init__(self):
        endpoint = urlsplit(self.dashboard_origin)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.path
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Webflow requires an exact HTTPS dashboard origin.")
        validate_recovery_generation(self.recovery_generation)

    def call(self, session_token, site_id, action, payload=None):
        return webflow_call(
            self.connection, session_token, self.recovery_generation, site_id, action, payload
        )

    def read(self, *, session_token, site_id):
        return {**self.call(session_token, site_id, "read"), "capabilities": dict(CAPABILITIES)}

    async def begin(self, *, session_token, site_id, provider_site, collection_id):
        object_id(provider_site)
        object_id(collection_id)
        # Authenticate before asking OpenBao for any credential.
        self.call(session_token, site_id, "preflight")
        client = await self.secrets_store.client(**self.openbao_options)
        attempt, state = uuid4(), secrets.token_urlsafe(32)
        redirect = self.dashboard_origin + "/auth/webflow/callback"
        self.call(
            session_token,
            site_id,
            "begin_oauth",
            {
                "id": str(attempt),
                "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
                "redirect_uri": redirect,
                "provider_site": provider_site,
                "collection_id": collection_id,
            },
        )
        return {
            "attempt_id": str(attempt),
            "authorization_url": "https://webflow.com/oauth/authorize?"
            + urlencode(
                {
                    "client_id": client["client_id"],
                    "response_type": "code",
                    "redirect_uri": redirect,
                    "state": state,
                    "scope": " ".join(sorted(SCOPES)),
                }
            ),
            "expires_in_seconds": 600,
        }

    async def complete(
        self, *, session_token, site_id, attempt_id, state, code, field_mapping, egress
    ):
        if (
            not isinstance(state, str)
            or len(state) != 43
            or not isinstance(code, str)
            or not 1 <= len(code) <= 2048
        ):
            raise WebflowUnavailable("WEBFLOW_OAUTH_REJECTED")
        redirect = self.dashboard_origin + "/auth/webflow/callback"
        consumed = self.call(
            session_token,
            site_id,
            "consume_oauth",
            {
                "attempt_id": str(attempt_id),
                "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
                "redirect_uri": redirect,
            },
        )
        client = await self.secrets_store.client(**self.openbao_options)
        reply = oauth_request(egress, {**client, "code": code, "redirect_uri": redirect})
        token = secret(reply.get("access_token"))
        scope = WebflowScope(consumed["provider_site"], consumed["collection_id"], token)
        provider = WebflowProvider(egress, scope)
        schema = self._validate_provider(provider, consumed["origin"], field_mapping)
        binding = uuid4()
        reference = await self.secrets_store.store(binding, token, **self.openbao_options)
        try:
            result = self.call(
                session_token,
                site_id,
                "bind",
                {
                    "id": str(binding),
                    "attempt_id": str(attempt_id),
                    "field_mapping": field_mapping,
                    "field_schema": schema,
                    "schema_sha256": schema_digest(schema),
                },
            )
            if result != {"state": "bound", "id": str(binding)}:
                raise WebflowUnavailable("WEBFLOW_BINDING_UNAVAILABLE")
        except Exception:
            await self.secrets_store.destroy(reference, **self.openbao_options)
            raise WebflowUnavailable("WEBFLOW_BINDING_UNAVAILABLE") from None
        return {"state": "bound", "id": str(binding)}

    @staticmethod
    def _validate_provider(provider, origin, mapping):
        validate_grant(provider.read("/v2/token/introspect"), provider.scope.site_id)
        published_domain(
            provider.read(f"/v2/sites/{provider.scope.site_id}/custom_domains"), origin
        )
        inventory = provider.read(f"/v2/sites/{provider.scope.site_id}/collections").get(
            "collections"
        )
        if (
            not isinstance(inventory, list)
            or len(inventory) > 100
            or sum(
                isinstance(c, dict) and c.get("id") == provider.scope.collection_id
                for c in inventory
            )
            != 1
        ):
            raise WebflowUnavailable("WEBFLOW_SITE_COLLECTION_MISMATCH")
        collection = provider.read(f"/v2/collections/{provider.scope.collection_id}")
        if collection.get("id") != provider.scope.collection_id:
            raise WebflowUnavailable("WEBFLOW_SITE_COLLECTION_MISMATCH")
        schema = collection.get("fields")
        validate_mapping(mapping, schema)
        return schema

    def seal(self, *, session_token, site_id, binding_id, candidate_id, source_sha256):
        # Replays must still match the currently approved exact article revision.
        options = self.call(session_token, site_id, "options")
        if not any(
            item["candidate_id"] == str(candidate_id) and item["source_sha256"] == source_sha256
            for item in options["articles"]
        ):
            raise WebflowUnavailable("WEBFLOW_OWNER_ACCESS_OR_SOURCE_UNAVAILABLE")
        self.call(session_token, site_id, "preflight")
        binding = self.call(session_token, site_id, "binding", {"binding_id": str(binding_id)})
        inventory = self.read(session_token=session_token, site_id=site_id)
        for item in inventory["inbox"]:
            if item["candidate_id"] == str(candidate_id):
                if item["binding_id"] != str(binding_id):
                    raise WebflowUnavailable("WEBFLOW_CANDIDATE_ALREADY_ASSIGNED")
                return {"state": "replayed", "id": item["id"]}
        # Allocate once; the unique candidate revision persists this operation and
        # its deterministic slug marker across retries and recovery.
        operation = uuid4()
        source = self.call(
            session_token,
            site_id,
            "source",
            {
                "id": str(operation),
                "binding_id": str(binding_id),
                "candidate_id": str(candidate_id),
                "source_sha256": source_sha256,
            },
        )
        payload = draft_body(source["article"], binding["field_mapping"], operation)
        canonical = canonical_json(payload)
        return self.call(
            session_token,
            site_id,
            "seal",
            {
                "id": str(operation),
                "binding_id": str(binding_id),
                "candidate_id": str(candidate_id),
                "source_sha256": source_sha256,
                "payload": payload,
                "canonical_hex": canonical.hex(),
                "revision_sha256": hashlib.sha256(canonical).hexdigest(),
            },
        )

    def review(self, *, session_token, site_id, revision_id, revision_sha256, decision):
        return self.call(
            session_token,
            site_id,
            "review",
            {
                "id": str(revision_id),
                "revision_sha256": revision_sha256,
                "decision": decision,
            },
        )

    async def _provider(self, binding, egress, digest=None):
        token = await self.secrets_store.read(binding["secret_reference"], **self.openbao_options)
        return WebflowProvider(
            egress, WebflowScope(binding["provider_site"], binding["collection_id"], token, digest)
        )

    async def reconcile(self, *, session_token, site_id, revision_id, egress):
        packet = self.call(session_token, site_id, "packet", {"id": str(revision_id)})
        if packet["state"] not in {"OUTCOME_UNKNOWN", "ESCALATED"}:
            return {"state": packet["state"]}
        provider = await self._provider(packet["binding"], egress)
        slug = packet["payload"]["items"][0]["fieldData"]["slug"]
        reply = provider.read(
            f"/v2/collections/{provider.scope.collection_id}/items?"
            + urlencode({"slug": slug, "limit": 2, "offset": 0})
        )
        state, identifier = reconcile_items(reply, packet["payload"])
        return self.call(
            session_token,
            site_id,
            "reconcile",
            {
                "id": str(revision_id),
                "state": state,
                "provider_item": identifier,
                "evidence_sha256": hashlib.sha256(canonical_json(reply)).hexdigest(),
            },
        )

    async def deliver(self, *, session_token, site_id, revision_id, egress):
        # There is no qualified live Webflow installation in this slice. This lab-only
        # switch is not a production authorization or a dashboard setting.
        if not self.disposable_test_writes or os.environ.get("SIGNAL_WEBFLOW_LAB") != "1":
            raise WebflowUnavailable(CAPABILITIES["production"])
        packet = self.call(session_token, site_id, "packet", {"id": str(revision_id)})
        if packet["state"] in {"OUTCOME_UNKNOWN", "ESCALATED", "DRAFT_RECORDED"}:
            return await self.reconcile(
                session_token=session_token, site_id=site_id, revision_id=revision_id, egress=egress
            )
        if self.write_journal is None:
            raise WebflowUnavailable("WEBFLOW_WRITE_JOURNAL_UNAVAILABLE")
        binding = packet["binding"]
        slug = packet["payload"]["items"][0]["fieldData"]["slug"]
        record = WebflowWriteIntentRecord(
            revision_id,
            site_id,
            UUID(binding["id"]),
            binding["provider_site"],
            binding["collection_id"],
            packet["revision_sha256"],
            slug,
        )
        try:
            _, entries = self.write_journal.verify_stream()
            prior = [r for r, _ in entries if r.operation_id == revision_id]
            if prior:
                if prior != [record]:
                    raise WebflowUnavailable("WEBFLOW_WRITE_INTENT_CONFLICT")
                self.call(session_token, site_id, "quarantine", {"id": str(revision_id)})
                return await self.reconcile(
                    session_token=session_token,
                    site_id=site_id,
                    revision_id=revision_id,
                    egress=egress,
                )
            if self.call(session_token, site_id, "eligibility", {"id": str(revision_id)}) != {
                "state": "eligible"
            }:
                raise WebflowUnavailable("WEBFLOW_APPROVAL_OR_SITE_QUARANTINE")
            # A restored/missing primary record never erases a possible external write.
            for r, _ in entries:
                if isinstance(r, WebflowWriteIntentRecord) and r.site_id == site_id:
                    old = self.call(session_token, site_id, "packet", {"id": str(r.operation_id)})
                    if old["state"] != "DRAFT_RECORDED":
                        raise WebflowUnavailable("WEBFLOW_SITE_QUARANTINED")
            provider = await self._provider(binding, egress, packet["revision_sha256"])
            schema = self._validate_provider(provider, binding["origin"], binding["field_mapping"])
            if schema_digest(schema) != binding["schema_sha256"][2:]:
                raise WebflowUnavailable("WEBFLOW_COLLECTION_SCHEMA_CHANGED")
            receipt = self.write_journal.append(record)
        except (WriteIntentJournalUnavailable, WriteIntentJournalConflict):
            raise WebflowUnavailable("WEBFLOW_WRITE_JOURNAL_UNAVAILABLE") from None
        nonce = uuid4()
        permit = self.call(
            session_token,
            site_id,
            "authorize",
            {
                "id": str(revision_id),
                "nonce": str(nonce),
                "journal_generation": str(receipt.generation),
                "journal_position": receipt.position,
                "journal_hash": receipt.body_hash,
            },
        )
        if permit != {"state": "AUTHORIZED"}:
            raise WebflowUnavailable("WEBFLOW_APPROVAL_OR_SITE_QUARANTINE")
        fenced = replace(
            provider,
            egress=replace(
                egress,
                fetcher=_OneShotFetcher(
                    egress.fetcher,
                    self,
                    session_token,
                    site_id,
                    revision_id,
                    nonce,
                    packet["revision_sha256"],
                ),
            ),
        )
        try:
            fenced.create_once(canonical_json(packet["payload"]), revision_id)
        except WebflowUnavailable:
            pass
        finally:
            self.call(session_token, site_id, "quarantine", {"id": str(revision_id)})
        return await self.reconcile(
            session_token=session_token, site_id=site_id, revision_id=revision_id, egress=egress
        )

    async def revoke(self, *, session_token, site_id, binding_id, egress=None):
        result = self.call(session_token, site_id, "revoke", {"id": str(binding_id)})
        upstream = "NOT_EXECUTED"
        try:
            if egress is not None:
                token = await self.secrets_store.read(
                    result["secret_reference"], **self.openbao_options
                )
                client = await self.secrets_store.client(**self.openbao_options)
                reply = oauth_request(egress, {**client, "access_token": token}, revoke=True)
                upstream = "accepted" if reply.get("did_revoke") is True else "OUTCOME_UNKNOWN"
        except WebflowUnavailable:
            upstream = "OUTCOME_UNKNOWN"
        await self.secrets_store.destroy(result["secret_reference"], **self.openbao_options)
        return {"state": result["state"], "event_id": result["event_id"], "upstream": upstream}
