"""Owner key lifecycle and fenced, verified-change IndexNow outbox dispatch."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from anyio import to_thread
from psycopg import Connection

from signal_core.authorization import AuthorizationDenied
from signal_core.crawl_frontier import claim_crawl_frontier
from signal_core.crawl_http import EgressHttpRequest
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import EgressProfile, profile_headers
from signal_core.indexnow_protocol import INDEXNOW_ENDPOINT, IndexNowSubmitScope, key_file_outcome
from signal_core.indexnow_secrets import IndexNowSecretUnavailable, OpenBaoIndexNowKeys
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import (
    ProviderEgressUnavailable,
    SharedEgressConflict,
    SharedEgressPermitExpired,
    SharedEgressProvider,
    SharedEgressReceipt,
    SharedEgressRequest,
    SharedEgressUnavailable,
    execute_shared_egress,
)
from signal_core.write_intent_journal import IndexNowWriteIntentRecord, WriteIntentJournal


class IndexNowUnavailable(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class IndexNowService:
    connection: Connection
    keys: OpenBaoIndexNowKeys
    generation: str

    def _call(self, name: str, token: object, site_id: UUID, *args):
        if not isinstance(site_id, UUID):
            raise AuthorizationDenied()
        generation = validate_recovery_generation(self.generation)
        with _clean_transaction(self.connection):
            return self.connection.execute(
                f"SELECT * FROM control.{name}(" + ",".join(["%s"] * (3 + len(args))) + ")",
                (hash_session_token(token), site_id, generation, *args),
            ).fetchone()

    async def create_key(
        self,
        *,
        session_token: object,
        site_id: UUID,
        key_id: UUID,
        previous_key_id: UUID | None = None,
        secret_options: dict | None = None,
    ) -> tuple[UUID, str, str]:
        if (
            not isinstance(key_id, UUID)
            or key_id.version != 4
            or (previous_key_id is not None and not isinstance(previous_key_id, UUID))
        ):
            raise ValueError("A stable typed key generation is required.")
        row = self._call("prepare_indexnow_key", session_token, site_id, key_id, previous_key_id)
        if not row or row[3] != "prepared":
            raise AuthorizationDenied()
        key = await self.keys.key(
            tenant_id=row[0], site_id=site_id, key_id=key_id, create=True, **(secret_options or {})
        )
        # Validate the site and key before recording its nonsecret digest.
        IndexNowSubmitScope(row[2], key, (row[2] + "/",))
        stored = self._call(
            "store_indexnow_key",
            session_token,
            site_id,
            key_id,
            hashlib.sha256(key.encode()).digest(),
        )
        if not stored or stored[0] != "stored":
            raise IndexNowUnavailable("KEY_RECORD_UNAVAILABLE")
        return row[0], row[2], key

    def read(self, *, session_token: object, site_id: UUID) -> dict:
        row = self._call("read_indexnow", session_token, site_id)
        if not row or row[1] != "found":
            raise AuthorizationDenied()
        return row[0]

    async def dispatch_one(
        self,
        *,
        session_token: object,
        site_id: UUID,
        worker_id: UUID,
        key_egress: SharedEgressProvider,
        submit_egress: SharedEgressProvider,
        journal: WriteIntentJournal,
        secret_options: dict | None = None,
    ) -> str:
        if (
            not isinstance(worker_id, UUID)
            or not isinstance(journal, WriteIntentJournal)
            or not isinstance(key_egress, SharedEgressProvider)
            or not isinstance(submit_egress, SharedEgressProvider)
            or key_egress.run.site_id != site_id
            or submit_egress.run.site_id != site_id
            or submit_egress.purpose != "connector"
        ):
            raise IndexNowUnavailable("INDEXNOW_EGRESS_UNAVAILABLE")
        attempt_id = uuid4()
        row = self._call("claim_indexnow", session_token, site_id, worker_id, attempt_id)
        if row is None:
            return "idle"
        if row[-1] == "denied":
            raise AuthorizationDenied()
        tenant_id, outbox_id, change_id, url, origin, key_id, key_hash, delivery_hash, reason = row
        if key_egress.run.tenant_id != tenant_id or submit_egress.run.tenant_id != tenant_id:
            raise IndexNowUnavailable("INDEXNOW_EGRESS_SCOPE_REJECTED")
        key_operation, submit_operation, status = None, None, None

        def finish(reason: str) -> str:
            result = self._call(
                "finish_indexnow",
                session_token,
                site_id,
                outbox_id,
                worker_id,
                attempt_id,
                key_operation,
                submit_operation,
                status,
                reason,
            )
            if not result or result[0] == "denied":
                raise IndexNowUnavailable("INDEXNOW_RECEIPT_UNAVAILABLE")
            return result[0]

        if reason != "claimed":
            return finish(reason)
        try:
            key = await self.keys.key(
                tenant_id=key_egress.run.tenant_id,
                site_id=site_id,
                key_id=key_id,
                **(secret_options or {}),
            )
            if hashlib.sha256(key.encode()).digest() != bytes(key_hash):
                return finish("EC_142_KEY_MISMATCH")
            scope = IndexNowSubmitScope(origin, key, (url,))
            if (
                key_egress.policy.allowed_origins != (origin,)
                or key_egress.policy.max_redirects != 0
            ):
                return finish("SUBMISSION_SCOPE_REJECTED")
        except IndexNowSecretUnavailable:
            return finish("INDEXNOW_SECRET_UNAVAILABLE")
        except ValueError:
            return finish("SUBMISSION_SCOPE_REJECTED")
        key_operation = uuid4()
        request = SharedEgressRequest(
            "crawl",
            EgressHttpRequest(
                "GET",
                scope.key_location,
                headers=profile_headers(EgressProfile.CRAWL_KEY_FILE, "GET", None),
                accepted_media_types=("text/plain",),
                max_response_bytes=1024,
                timeout_seconds=5,
            ),
            EgressProfile.CRAWL_KEY_FILE,
        )
        try:
            lease = await to_thread.run_sync(
                lambda: claim_crawl_frontier(
                    key_egress.admission_connection,
                    key_egress.run,
                    worker_key=key_egress.worker_key,
                    lease_id=key_operation,
                    lease_seconds=30,
                )
            )
            if lease is None or lease.url.fetch_url != scope.key_location:
                key_operation = None
                return finish("EC_142_KEY_UNREACHABLE")
            result = await to_thread.run_sync(
                lambda: execute_shared_egress(
                    key_egress.admission_connection,
                    key_egress.ingest_connection,
                    key_egress.store,
                    key_egress.run,
                    key_egress.policy,
                    key_egress.fetcher,
                    request,
                    operation_id=key_operation,
                    worker_key=key_egress.worker_key,
                    admission_policy=key_egress.admission_policy,
                    artifact_key=key_egress.artifact_key,
                )
            )
        except (SharedEgressUnavailable, SharedEgressConflict, SharedEgressPermitExpired):
            # An uncommitted egress identity cannot be a receipt foreign key.
            key_operation = None
            return finish("EC_142_KEY_UNREACHABLE")
        if not isinstance(result, SharedEgressReceipt):
            key_operation = None
            return finish("EC_142_KEY_UNREACHABLE")
        response = result.response
        reason = key_file_outcome(
            scope,
            status=response.http_status,
            media_type=response.media_type,
            body=response.body,
            final_url=response.final_url,
            outcome=response.outcome,
        )
        if reason != "KEY_DEPLOYED":
            return finish(reason)
        submit_operation = uuid4()
        body = scope.body()
        _, prior = await to_thread.run_sync(journal.verify_stream)
        for record, acknowledgement in prior:
            if isinstance(record, IndexNowWriteIntentRecord) and (
                record.site_id == site_id and record.change_id == change_id and record.url == url
            ):
                known = self._call(
                    "indexnow_known_effect",
                    session_token,
                    site_id,
                    record.operation_id,
                    acknowledgement.generation,
                    acknowledgement.position,
                    bytes.fromhex(acknowledgement.body_hash),
                )
                if known != (True,):
                    submit_operation = None
                    return finish("POST_OUTCOME_UNKNOWN")
        intent = IndexNowWriteIntentRecord(
            submit_operation,
            site_id,
            change_id,
            bytes(delivery_hash).hex(),
            bytes(key_hash).hex(),
            url,
            hashlib.sha256(body).hexdigest(),
        )
        # Independent journal acknowledgement precedes the primary dispatch fence.
        acknowledgement = await to_thread.run_sync(lambda: journal.append(intent))
        permitted = self._call(
            "begin_indexnow_submit",
            session_token,
            site_id,
            outbox_id,
            worker_id,
            attempt_id,
            key_operation,
            acknowledgement.generation,
            acknowledgement.position,
            bytes.fromhex(acknowledgement.body_hash),
            hashlib.sha256(body).digest(),
        )
        if not permitted or permitted[0] != "permitted":
            submit_operation = None
            return finish("SUBMISSION_SCOPE_REJECTED")
        try:
            provider = await to_thread.run_sync(
                lambda: submit_egress.request_json(
                    method="POST",
                    url=INDEXNOW_ENDPOINT,
                    profile=EgressProfile.INDEXNOW_SUBMIT,
                    indexnow_scope=scope,
                    authorization=None,
                    body=body,
                    operation_id=submit_operation,
                    timeout_seconds=5,
                    max_response_bytes=4096,
                )
            )
            status = provider.status_code
        except ProviderEgressUnavailable:
            # No provider idempotency-token or reconciliation API: never replay an uncertain POST.
            submit_operation = None
            return finish("POST_OUTCOME_UNKNOWN")
        return finish("KEY_DEPLOYED")
