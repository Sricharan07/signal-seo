"""Owner/MFA Google Docs sync; credentials never enter database projections."""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from psycopg import Connection, Error
from psycopg.types.json import Jsonb

from signal_core.brand_documents import (
    BrandDocumentUnavailable,
    read_brand_document,
    upload_brand_document,
)
from signal_core.business_brain import FactProvenance
from signal_core.business_brain_extraction import BusinessBrainExtractor
from signal_core.connector_framework import (
    begin_pkce,
    connector_row,
    consume_pkce,
    public_projection,
    state_digest,
)
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.database import _clean_transaction
from signal_core.docs_protocol import (
    DocsRejected,
    docs_authorization,
    export_text,
    picked_files,
    read_metadata,
)
from signal_core.docs_secrets import OpenBaoDocsSecrets
from signal_core.egress_profiles import DrivePickedScope
from signal_core.gsc_oauth import (
    DRIVE_FILE_SCOPE,
    GscOAuthError,
    exchange_gsc_code,
    refresh_gsc_access_token,
)
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import SharedEgressProvider


@dataclass(frozen=True, repr=False)
class DocsService:
    connection: Connection = field(repr=False)
    secrets_store: OpenBaoDocsSecrets
    store: EncryptedLocalArtifactStore
    key: ArtifactEncryptionKey
    recovery_generation: str
    redirect_uri: str
    current_generation: Callable[[], Awaitable[str]] | None = field(default=None, repr=False)
    extractor: BusinessBrainExtractor | None = field(default=None, repr=False)

    async def _fresh(self) -> None:
        if (
            self.current_generation is not None
            and await self.current_generation() != self.recovery_generation
        ):
            raise DocsRejected("DOCS_RECOVERY_CHANGED")

    def _one(self, name, *args):
        return connector_row(
            self.connection, name, *args, scalar=True, transaction=_clean_transaction
        )

    def _args(self, token: str, site_id: UUID):
        return hash_session_token(token), self.recovery_generation, site_id

    def packet(self, token: str, site_id: UUID) -> dict:
        try:
            return self._one("docs_binding_packet", *self._args(token, site_id))
        except Error:
            raise DocsRejected("DOCS_OWNER_DENIED") from None

    def status(self, token: str, site_id: UUID) -> dict:
        packet = self.packet(token, site_id)
        public_projection(packet, ("secret_reference", "tenant_id"))
        packet["extraction_availability"] = (
            "available"
            if self.extractor is not None and self.extractor.configured
            else "unavailable"
        )
        return packet

    async def begin(self, token: str, site_id: UUID) -> dict:
        await self._fresh()
        self.packet(token, site_id)
        credentials = await self.secrets_store.client_credentials()
        authorization = docs_authorization(
            client_id=credentials.client_id, redirect_uri=self.redirect_uri
        )
        attempt = uuid4()

        async def persist():
            await self._fresh()
            return self._one(
                "begin_docs_oauth",
                *self._args(token, site_id),
                attempt,
                authorization.state_sha256,
                authorization.code_challenge,
                self.redirect_uri,
            )

        await begin_pkce(
            self.secrets_store, attempt, authorization, persist, DocsRejected("DOCS_OWNER_DENIED")
        )
        return {"attempt_id": attempt, "authorization_url": authorization.url}

    async def complete(
        self,
        token: str,
        site_id: UUID,
        *,
        attempt_id: UUID,
        state: str,
        code: str,
        picked_file_ids: str,
        egress: SharedEgressProvider,
    ) -> dict:
        await self._fresh()
        files = picked_files(picked_file_ids)
        digest = state_digest(state, DocsRejected("DOCS_STATE_REJECTED"))
        challenge = self._one(
            "consume_docs_oauth",
            *self._args(token, site_id),
            attempt_id,
            digest,
            self.redirect_uri,
        )
        if challenge is None:
            raise DocsRejected("DOCS_ATTEMPT_UNAVAILABLE")
        verifier = await consume_pkce(
            self.secrets_store, attempt_id, challenge, DocsRejected("DOCS_PKCE_REJECTED")
        )
        credentials = await self.secrets_store.client_credentials()
        await self._fresh()
        self._attempt_current(token, site_id, attempt_id)
        tokens = exchange_gsc_code(
            egress=egress,
            credentials=credentials,
            code=code,
            verifier=verifier,
            redirect_uri=self.redirect_uri,
            operation_id=uuid4(),
            scope=DRIVE_FILE_SCOPE,
        )
        reference = None
        try:
            scope = DrivePickedScope(files, tokens.access_token)
            for file_id in files:
                await self._fresh()
                self._attempt_current(token, site_id, attempt_id)
                metadata = read_metadata(egress, scope, file_id, uuid4())
                if metadata.trashed:
                    raise DocsRejected("DOCS_PICKER_REJECTED")
            if tokens.refresh_token is None:
                raise DocsRejected("DOCS_REFRESH_UNAVAILABLE")
            reference = await self.secrets_store.store_refresh_token(
                attempt_id, tokens.refresh_token
            )
            await self._fresh()
            binding_id = uuid4()
            outcome = self._one(
                "bind_docs_oauth",
                *self._args(token, site_id),
                attempt_id,
                binding_id,
                Jsonb(list(files)),
                DRIVE_FILE_SCOPE,
            )
            if outcome != "bound":
                raise DocsRejected("DOCS_BINDING_REJECTED")
        except Exception:
            if reference is not None:
                try:
                    await self.secrets_store.destroy_refresh_token(reference)
                except Exception:
                    pass
            raise
        return {"binding_id": binding_id}

    async def disconnect(self, token: str, site_id: UUID, binding_id: UUID) -> dict:
        await self._fresh()
        packet = self.packet(token, site_id)
        if packet.get("binding_id") != str(binding_id):
            raise DocsRejected("DOCS_BINDING_UNAVAILABLE")
        result = self._one("revoke_docs_binding", *self._args(token, site_id), binding_id)
        if result not in {"revoked_pending", "revoked_durable"}:
            raise DocsRejected("DOCS_OWNER_DENIED")
        cleanup = False
        try:
            await self.secrets_store.destroy_refresh_token(packet["secret_reference"])
            cleanup = True
        except Exception:
            pass
        return {"state": result, "secret_removed": cleanup}

    async def sync(self, token: str, site_id: UUID, egress: SharedEgressProvider) -> dict:
        await self._fresh()
        packet = self.packet(token, site_id)
        if packet["availability"] != "ready":
            raise DocsRejected("DOCS_BINDING_UNAVAILABLE")
        binding = UUID(packet["binding_id"])
        lock = int.from_bytes(hashlib.sha256(binding.bytes).digest()[:8], "big", signed=True)
        if not self.connection.execute("SELECT pg_try_advisory_lock(%s)", (lock,)).fetchone()[0]:
            raise DocsRejected("DOCS_SYNC_BUSY")
        try:
            self._current(token, site_id, binding)
            credentials = await self.secrets_store.client_credentials()
            previous = await self.secrets_store.refresh_token(packet["secret_reference"])
            await self._fresh()
            self._current(token, site_id, binding)
            try:
                tokens = refresh_gsc_access_token(
                    egress=egress,
                    credentials=credentials,
                    refresh_token=previous,
                    operation_id=uuid4(),
                    scope=DRIVE_FILE_SCOPE,
                )
                if tokens.refresh_token is not None and tokens.refresh_token != previous:
                    await self.secrets_store.replace_refresh_token(
                        packet["secret_reference"], previous, tokens.refresh_token
                    )
            except Exception as error:
                reason = (
                    "scope_changed"
                    if isinstance(error, GscOAuthError) and error.code == "GSC_REDUCED_SCOPE"
                    else "refresh_failed"
                )
                self._one("degrade_docs_binding", *self._args(token, site_id), binding, reason)
                raise DocsRejected("DOCS_REAUTH_REQUIRED") from None
            scope = DrivePickedScope(
                tuple(x["file_id"] for x in packet["sources"]), tokens.access_token
            )
            for source in packet["sources"]:
                await self._fresh()
                if source["state"] == "withdrawn":
                    continue
                source_id = UUID(source["source_id"])
                try:
                    self._current(token, site_id, binding)
                    metadata = read_metadata(egress, scope, source["file_id"], uuid4())
                    if metadata.trashed:
                        self._withdraw(token, site_id, binding, source_id, "provider_removed")
                        continue
                    if (
                        metadata.version == source["provider_version"]
                        and source["modified_time"] is not None
                        and metadata.modified_time
                        == datetime.fromisoformat(source["modified_time"])
                    ):
                        if source["document_id"]:
                            await self._propose(
                                token, site_id, binding, UUID(source["document_id"])
                            )
                        continue
                    self._current(token, site_id, binding)
                    body = export_text(egress, scope, source["file_id"], uuid4())
                    await self._fresh()
                    self._current(token, site_id, binding)
                    after = read_metadata(egress, scope, source["file_id"], uuid4())
                    if after != metadata:
                        raise DocsRejected("DOCS_VERSION_CHANGED_DURING_EXPORT")
                    await self._fresh()
                except DocsRejected as error:
                    if error.code == "DOCS_PROVIDER_UNSHARED":
                        self._withdraw(token, site_id, binding, source_id, "provider_unshared")
                        continue
                    if error.code == "DOCS_REAUTH_REQUIRED":
                        self._one(
                            "degrade_docs_binding",
                            *self._args(token, site_id),
                            binding,
                            "provider_reauthorization",
                        )
                    raise

                def register(connection, document_id, source_id=source_id, metadata=metadata):
                    result = connection.execute(
                        "SELECT control.record_docs_source_version(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            *self._args(token, site_id),
                            binding,
                            source_id,
                            metadata.version,
                            metadata.modified_time,
                            document_id,
                            None,
                        ),
                    ).fetchone()[0]
                    if result != "recorded":
                        raise DocsRejected("DOCS_VERSION_UNAVAILABLE")

                document = upload_brand_document(
                    self.connection,
                    self.store,
                    self.key,
                    session_token=token,
                    current_recovery_generation=self.recovery_generation,
                    site_id=site_id,
                    filename="Google document.txt",
                    body=body,
                    supersedes_id=UUID(source["document_id"]) if source["document_id"] else None,
                    on_registered=register,
                )
                await self._propose(token, site_id, binding, document.document_id)
            return self.status(token, site_id)
        finally:
            self.connection.execute("SELECT pg_advisory_unlock(%s)", (lock,))

    async def _propose(self, token: str, site_id: UUID, binding: UUID, document: UUID) -> None:
        if self.extractor is None or not self.extractor.configured:
            return
        await self._fresh()
        self._current(token, site_id, binding)
        try:
            source = read_brand_document(
                self.connection,
                self.store,
                self.key,
                session_token=token,
                current_recovery_generation=self.recovery_generation,
                site_id=site_id,
                document_id=document,
            )
        except BrandDocumentUnavailable as error:
            if str(error) == "document_contains_secret":
                return
            raise
        # The existing extraction port accepts at most 24,000 characters per exact range.
        for start in range(0, len(source.text), 24000):
            await self._fresh()
            self._current(token, site_id, binding)
            result = await self.extractor.extract(
                self.connection,
                session_token=token,
                current_recovery_generation=self.recovery_generation,
                site_id=site_id,
                provenance=FactProvenance(
                    "brand_document",
                    document,
                    {"start": start, "end": min(start + 24000, len(source.text))},
                ),
            )
            if result.state != "completed":
                break

    def _current(self, token: str, site_id: UUID, binding: UUID) -> None:
        packet = self.packet(token, site_id)
        if packet.get("binding_id") != str(binding) or packet["availability"] != "ready":
            raise DocsRejected("DOCS_BINDING_UNAVAILABLE")

    def _attempt_current(self, token: str, site_id: UUID, attempt: UUID) -> None:
        if self._one("docs_oauth_current", *self._args(token, site_id), attempt) is not True:
            raise DocsRejected("DOCS_ATTEMPT_UNAVAILABLE")

    def _withdraw(self, token: str, site_id: UUID, binding: UUID, source: UUID, reason: str):
        result = self._one(
            "record_docs_source_version",
            *self._args(token, site_id),
            binding,
            source,
            None,
            None,
            None,
            reason,
        )
        if result != "withdrawn":
            raise DocsRejected("DOCS_SOURCE_UNAVAILABLE")
