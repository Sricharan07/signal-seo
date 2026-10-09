"""Owner-only GA4 orchestration reuses the Google PKCE and OpenBao lifecycle."""

import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import date
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.connector_framework import (
    begin_pkce,
    connector_row,
    consume_pkce,
    external_revocation,
    refresh_lock,
    require_restriction,
    state_digest,
)
from signal_core.database import _clean_transaction
from signal_core.ga4_protocol import (
    GA4_SCOPE,
    Ga4Error,
    discover_ga4_properties,
    query_ga4_report,
    verify_ga4_stream,
)
from signal_core.ga4_secrets import OpenBaoGa4Secrets
from signal_core.gsc_oauth import (
    GscOAuthError,
    exchange_gsc_code,
    new_gsc_authorization,
    refresh_gsc_access_token,
    revoke_gsc_refresh_token,
)
from signal_core.gsc_secrets import GscSecretError
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token
from signal_core.shared_egress import SharedEgressProvider


@dataclass(repr=False)
class Ga4Service:
    connection: Connection
    secrets_store: OpenBaoGa4Secrets
    recovery_generation: str
    redirect_uri: str
    current_generation: Callable[[], Awaitable[str]] | None = None

    async def _current(self):
        if self.current_generation is not None:
            if await self.current_generation() != self.recovery_generation:
                raise Ga4Error("GA4_RECOVERY_CHANGED")

    def _one(self, name, *args):
        return connector_row(
            self.connection, name, *args, scalar=False, transaction=_clean_transaction
        )

    def _args(self, session_token, site_id):
        try:
            return hash_session_token(session_token), self.recovery_generation, site_id
        except InvalidOpaqueSessionToken:
            raise Ga4Error("GA4_SESSION_REJECTED") from None

    def read(self, session_token: str, site_id: UUID):
        row = self._one("ga4_status", *self._args(session_token, site_id))
        if row is None or row[0] is None:
            raise Ga4Error("GA4_OWNER_MFA_OR_ORIGIN_DENIED")
        return row[0]

    async def begin(self, session_token: str, site_id: UUID):
        await self._current()
        self.read(session_token, site_id)
        credentials = await self.secrets_store.client_credentials()
        authorization = new_gsc_authorization(
            client_id=credentials.client_id, redirect_uri=self.redirect_uri, scope=GA4_SCOPE
        )
        attempt = uuid4()

        async def persist():
            await self._current()
            row = self._one(
                "begin_ga4_oauth_attempt",
                *self._args(session_token, site_id),
                attempt,
                authorization.state_sha256,
                self.redirect_uri,
                authorization.code_challenge,
            )
            return row[0] if row else None

        await begin_pkce(
            self.secrets_store,
            attempt,
            authorization,
            persist,
            Ga4Error("GA4_OWNER_MFA_OR_ORIGIN_DENIED"),
        )
        return {"attempt_id": attempt, "authorization_url": authorization.url}

    async def complete(
        self,
        session_token: str,
        site_id: UUID,
        *,
        attempt_id: UUID,
        state: str,
        code: str,
        egress: SharedEgressProvider,
    ):
        await self._current()
        args = self._args(session_token, site_id)
        digest = state_digest(state, Ga4Error("GA4_STATE_REJECTED"))
        row = self._one(
            "consume_ga4_oauth_attempt",
            *args,
            attempt_id,
            digest,
            self.redirect_uri,
        )
        if row is None or row[0] != "consumed":
            raise Ga4Error("GA4_ATTEMPT_UNAVAILABLE")
        verifier = await consume_pkce(
            self.secrets_store, attempt_id, row[2], Ga4Error("GA4_PKCE_REJECTED")
        )
        tokens = exchange_gsc_code(
            egress=egress,
            credentials=await self.secrets_store.client_credentials(),
            code=code,
            verifier=verifier,
            redirect_uri=self.redirect_uri,
            operation_id=uuid4(),
            scope=GA4_SCOPE,
        )
        properties = discover_ga4_properties(
            egress=egress, access_token=tokens.access_token, operation_id=uuid4()
        )
        if tokens.refresh_token is None or not properties:
            raise Ga4Error("GA4_NO_PROPERTY")
        candidates = [asdict(item) for item in properties]
        if tokens.refresh_token in json.dumps(candidates):
            raise Ga4Error("GA4_RESPONSE_REJECTED")
        reference = await self.secrets_store.store_refresh_token(attempt_id, tokens.refresh_token)
        try:
            await self._current()
            if self._one("stage_ga4_oauth_attempt", *args, attempt_id, Jsonb(candidates)) != (
                "staged",
            ):
                raise Ga4Error("GA4_ATTEMPT_UNAVAILABLE")
        except Exception:
            await self.secrets_store.destroy_refresh_token(reference)
            raise
        return {"attempt_id": attempt_id, "properties": candidates}

    async def _refresh(self, reference, egress, operation):
        refresh = await self.secrets_store.refresh_token(reference)
        tokens = refresh_gsc_access_token(
            egress=egress,
            credentials=await self.secrets_store.client_credentials(),
            refresh_token=refresh,
            operation_id=operation,
            scope=GA4_SCOPE,
        )
        if tokens.refresh_token is not None and tokens.refresh_token != refresh:
            await self.secrets_store.replace_refresh_token(reference, refresh, tokens.refresh_token)
        return tokens.access_token, (refresh, tokens.refresh_token)

    async def confirm(
        self,
        session_token: str,
        site_id: UUID,
        *,
        attempt_id: UUID,
        property_resource_name: str,
        egress: SharedEgressProvider,
    ):
        await self._current()
        args = self._args(session_token, site_id)
        selection = self._one("ga4_selection", *args, attempt_id, property_resource_name)
        if selection is None:
            raise Ga4Error("GA4_PROPERTY_DENIED")
        async with self._refresh_lock(attempt_id):
            access, _ = await self._refresh(selection[2], egress, uuid4())
            operation, digest = verify_ga4_stream(
                egress=egress,
                access_token=access,
                property_resource_name=property_resource_name,
                verified_origin=selection[1],
                operation_id=uuid4(),
            )
            binding = uuid4()
            await self._current()
            row = self._one(
                "confirm_ga4_binding",
                *args,
                attempt_id,
                binding,
                property_resource_name,
                selection[1],
                operation,
                digest,
            )
        if row != ("bound",):
            raise Ga4Error("GA4_BINDING_REJECTED")
        return {
            "binding_id": binding,
            "property_resource_name": property_resource_name,
            "state": "read_only",
        }

    def _refresh_lock(self, identifier):
        @asynccontextmanager
        async def lock():
            with refresh_lock(self.connection, identifier, Ga4Error("GA4_REFRESH_BUSY")):
                yield

        return lock()

    async def import_report(
        self,
        session_token: str,
        site_id: UUID,
        *,
        start_date: date,
        end_date: date,
        egress: SharedEgressProvider,
    ):
        await self._current()
        if not 0 <= (end_date - start_date).days <= 92:
            raise Ga4Error("GA4_DATE_RANGE_REJECTED")
        args = self._args(session_token, site_id)
        binding = self._one("ga4_owner_binding", *args)
        if binding is None:
            raise Ga4Error("GA4_BINDING_UNAVAILABLE")
        async with self._refresh_lock(binding[1]):
            if self._one("ga4_owner_binding", *args) != binding:
                raise Ga4Error("GA4_BINDING_UNAVAILABLE")
            refresh_op = uuid4()
            try:
                access, refresh = await self._refresh(binding[3], egress, refresh_op)
            except (GscOAuthError, GscSecretError) as error:
                if error.code in {
                    "GSC_REAUTH_REQUIRED",
                    "GSC_REDUCED_SCOPE",
                    "GSC_SECRET_WRITE_FAILED",
                    "GSC_REFRESH_TOKEN_CONFLICT",
                }:
                    self._one("ga4_owner_reauth", *args, binding[1], uuid4(), refresh_op)
                raise
            report_operation = uuid4()
            try:
                report = query_ga4_report(
                    egress=egress,
                    access_token=access,
                    property_resource_name=binding[2],
                    start_date=start_date,
                    end_date=end_date,
                    operation_id=report_operation,
                )
            except Ga4Error as error:
                if error.code == "GA4_REAUTH_REQUIRED":
                    self._one("ga4_owner_reauth", *args, binding[1], uuid4(), error.operation_id)
                raise
            if any(
                value and value in json.dumps({"rows": report.rows, "coverage": report.coverage})
                for value in refresh
            ):
                raise Ga4Error("GA4_RESPONSE_REJECTED")
            generation = uuid4()
            await self._current()
            row = self._one(
                "record_ga4_owner_import",
                *args,
                generation,
                binding[1],
                binding[2],
                start_date,
                end_date,
                Jsonb(list(report.rows)),
                Jsonb(report.coverage),
                Jsonb(
                    [
                        {"operation_id": str(op), "response_sha256": digest.hex()}
                        for op, digest in report.receipts
                    ]
                ),
            )
            if row != ("recorded",):
                raise Ga4Error("GA4_IMPORT_REJECTED")
        return {"generation_id": generation, "coverage": report.coverage, "state": "imported"}

    async def disconnect(
        self, session_token: str, site_id: UUID, *, binding_id: UUID, egress: SharedEgressProvider
    ):
        await self._current()
        row = self._one(
            "revoke_ga4_binding", *self._args(session_token, site_id), binding_id, uuid4()
        )
        require_restriction(
            row, {"revoked", "AUTHORITY_DURABILITY_PENDING"}, Ga4Error("GA4_DISCONNECT_DENIED")
        )

        async def revoke():
            refresh = await self.secrets_store.refresh_token(row[1])
            return revoke_gsc_refresh_token(
                egress=egress, refresh_token=refresh, operation_id=uuid4()
            )

        upstream = await external_revocation(
            revoke,
            lambda: self.secrets_store.destroy_refresh_token(row[1]),
            caught=(GscOAuthError, GscSecretError),
            failure=False,
        )
        return {"state": row[0], "upstream_revoked": upstream}
