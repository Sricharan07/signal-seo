"""Owner-confirmed Search Console binding through OpenBao and shared egress."""

import hashlib
from dataclasses import dataclass
from datetime import date
from functools import partial
from uuid import UUID, uuid4

from anyio import to_thread
from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.connector_framework import (
    OAuthBinding,
    external_revocation,
    refresh_oauth,
    require_restriction,
)
from signal_core.database import _clean_transaction
from signal_core.gsc_oauth import (
    GscAnalytics,
    GscOAuthError,
    exchange_gsc_code,
    new_gsc_authorization,
    query_gsc_analytics,
    refresh_gsc_access_token,
    revoke_gsc_refresh_token,
)
from signal_core.gsc_properties import (
    GscProperty,
    discover_gsc_properties_via_egress,
)
from signal_core.gsc_secrets import GscSecretError, OpenBaoGscSecrets
from signal_core.session_tokens import session_token_hasher
from signal_core.shared_egress import SharedEgressProvider


class GscBindingError(Exception):
    """Fixed owner-facing outcome without token, code, or provider body."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class GscAuthorizationStarted:
    attempt_id: UUID
    authorization_url: str
    expires_in_seconds: int = 600


@dataclass(frozen=True, repr=False)
class GscPropertySelection:
    attempt_id: UUID
    properties: tuple[GscProperty, ...]


@dataclass(frozen=True, repr=False)
class GscBinding:
    binding_id: UUID
    property_resource_name: str


@dataclass(frozen=True, repr=False)
class GscImportGeneration:
    generation_id: UUID
    property_resource_name: str
    analytics: GscAnalytics


async def begin_gsc_authorization(
    connection: Connection,
    secrets_store: OpenBaoGscSecrets,
    *,
    session_token: str,
    recovery_generation: str,
    site_id: UUID,
    redirect_uri: str,
    openbao_options: dict | None = None,
) -> GscAuthorizationStarted:
    options = dict(openbao_options or {})
    args = (_session_hash(session_token), recovery_generation, site_id)
    credentials = await secrets_store.client_credentials(**options)
    authorization = new_gsc_authorization(
        client_id=credentials.client_id, redirect_uri=redirect_uri
    )
    attempt_id = uuid4()
    await _lifecycle(connection, secrets_store).begin(
        args, authorization, redirect_uri, attempt_id, **options
    )
    return GscAuthorizationStarted(attempt_id, authorization.url)


async def complete_gsc_authorization(
    connection: Connection,
    secrets_store: OpenBaoGscSecrets,
    egress: SharedEgressProvider,
    *,
    session_token: str,
    recovery_generation: str,
    site_id: UUID,
    attempt_id: UUID,
    state: str,
    code: str,
    redirect_uri: str,
    token_operation_id: UUID,
    discovery_operation_id: UUID,
    openbao_options: dict | None = None,
    allowed_property_resources: frozenset[str] | None = None,
) -> GscPropertySelection:
    options = dict(openbao_options or {})
    if allowed_property_resources is not None and (
        not isinstance(allowed_property_resources, frozenset)
        or not 1 <= len(allowed_property_resources) <= 16
        or any(
            not isinstance(value, str) or not 1 <= len(value) <= 2048
            for value in allowed_property_resources
        )
    ):
        raise ValueError("A bounded property resource allowlist is required.")
    args = (_session_hash(session_token), recovery_generation, site_id)

    def validate(tokens, properties):
        if allowed_property_resources is not None:
            properties = tuple(
                item
                for item in properties
                if item.eligible and item.resource_name in allowed_property_resources
            )
        if not any(item.eligible for item in properties):
            raise GscBindingError("GSC_NO_ELIGIBLE_PROPERTY")
        if tokens.refresh_token is None:
            raise GscBindingError("GSC_REFRESH_TOKEN_UNAVAILABLE")
        return properties

    properties = await _lifecycle(connection, secrets_store).complete(
        args,
        attempt_id,
        state,
        redirect_uri,
        lambda credentials, verifier: _provider(
            exchange_gsc_code,
            egress=egress,
            credentials=credentials,
            code=code,
            verifier=verifier,
            redirect_uri=redirect_uri,
            operation_id=token_operation_id,
        ),
        lambda tokens, origin: _provider(
            discover_gsc_properties_via_egress,
            access_token=tokens.access_token,
            verified_origin=origin,
            egress=egress,
            operation_id=discovery_operation_id,
        ),
        lambda items: Jsonb(
            [
                {
                    "resource_name": item.resource_name,
                    "property_type": item.property_type,
                    "eligible": item.eligible,
                }
                for item in items
            ]
        ),
        validate,
        **options,
    )
    return GscPropertySelection(attempt_id, properties)


def confirm_gsc_binding(
    connection: Connection,
    *,
    session_token: str,
    recovery_generation: str,
    site_id: UUID,
    attempt_id: UUID,
    property_resource_name: str,
) -> GscBinding:
    binding = _lifecycle(connection, None).confirm(
        (_session_hash(session_token), recovery_generation, site_id),
        attempt_id,
        property_resource_name,
    )
    return GscBinding(binding, property_resource_name)


async def revoke_gsc_binding(
    connection: Connection,
    secrets_store: OpenBaoGscSecrets,
    *,
    session_token: str,
    recovery_generation: str,
    site_id: UUID,
    binding_id: UUID,
    egress: SharedEgressProvider,
    revocation_operation_id: UUID,
    openbao_options: dict | None = None,
) -> bool:
    options = dict(openbao_options or {})
    token_hash = _session_hash(session_token)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT outcome, secret_reference FROM control.revoke_gsc_binding(%s, %s, %s, %s, %s)",
            (token_hash, recovery_generation, site_id, binding_id, uuid4()),
        ).fetchone()
    require_restriction(row, {"revoked"}, GscBindingError("GSC_REVOKE_DENIED"))

    async def revoke():
        refresh_token = await secrets_store.refresh_token(row[1], **options)
        return await _provider(
            revoke_gsc_refresh_token,
            egress=egress,
            refresh_token=refresh_token,
            operation_id=revocation_operation_id,
        )

    return await external_revocation(
        revoke, lambda: secrets_store.destroy_refresh_token(row[1], **options)
    )


async def import_gsc_analytics(
    connection: Connection,
    secrets_store: OpenBaoGscSecrets,
    egress: SharedEgressProvider,
    *,
    tenant_id: UUID,
    site_id: UUID,
    start_date: date,
    end_date: date,
    dimensions: tuple[str, ...],
    refresh_operation_id: UUID,
    query_operation_id: UUID,
    data_state: str = "all",
    openbao_options: dict | None = None,
) -> GscImportGeneration:
    if data_state not in {"all", "final"}:
        raise GscBindingError("GSC_QUERY_REJECTED")
    options = dict(openbao_options or {})
    with _clean_transaction(connection):
        binding = connection.execute(
            "SELECT binding_id, property_resource_name, secret_reference, origin "
            "FROM control.current_gsc_binding(%s, %s)",
            (tenant_id, site_id),
        ).fetchone()
    if binding is None:
        raise GscBindingError("GSC_BINDING_UNAVAILABLE")
    lock_key = int.from_bytes(hashlib.sha256(binding[0].bytes).digest()[:8], "big", signed=True)
    locked = connection.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,)).fetchone()[0]
    if not locked:
        raise GscBindingError("GSC_REFRESH_BUSY")
    try:
        with _clean_transaction(connection):
            current = connection.execute(
                "SELECT binding_id FROM control.current_gsc_binding(%s, %s)",
                (tenant_id, site_id),
            ).fetchone()
        if current is None or current[0] != binding[0]:
            raise GscBindingError("GSC_BINDING_UNAVAILABLE")
        tokens = await refresh_oauth(
            secrets_store,
            binding[2],
            lambda credentials, refresh: _provider(
                refresh_gsc_access_token,
                egress=egress,
                credentials=credentials,
                refresh_token=refresh,
                operation_id=refresh_operation_id,
            ),
            lambda reason: _mark_reauth(
                connection, tenant_id, site_id, binding[0], refresh_operation_id, reason
            ),
            protocol_error=GscOAuthError,
            reauth_codes={
                "GSC_REAUTH_REQUIRED": "provider_revoked",
                "GSC_REDUCED_SCOPE": "reduced_scope",
            },
            secret_error=GscSecretError,
            rotation_error=GscBindingError("GSC_ROTATION_UNPERSISTED"),
            options=options,
        )

    finally:
        connection.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
    try:
        analytics = await _provider(
            query_gsc_analytics,
            egress=egress,
            access_token=tokens.access_token,
            property_resource_name=binding[1],
            start_date=start_date,
            end_date=end_date,
            dimensions=dimensions,
            operation_id=query_operation_id,
            data_state=data_state,
        )
    except GscOAuthError as error:
        if error.code == "GSC_REAUTH_REQUIRED":
            _mark_reauth(
                connection,
                tenant_id,
                site_id,
                binding[0],
                query_operation_id,
                "provider_revoked",
            )
        raise
    generation_id = uuid4()
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.record_gsc_import_generation(" + ", ".join(["%s"] * 15) + ")",
            (
                tenant_id,
                site_id,
                generation_id,
                binding[0],
                binding[1],
                "web",
                Jsonb(list(dimensions)),
                start_date,
                end_date,
                data_state,
                analytics.aggregation_type,
                Jsonb(list(analytics.rows)),
                Jsonb(analytics.coverage),
                analytics.response_sha256,
                query_operation_id,
            ),
        ).fetchone()[0]
    if outcome != "recorded":
        raise GscBindingError("GSC_IMPORT_REJECTED")
    return GscImportGeneration(generation_id, binding[1], analytics)


async def _provider(function, **kwargs):
    return await to_thread.run_sync(partial(function, **kwargs))


_session_hash = session_token_hasher(lambda: GscBindingError("GSC_SESSION_REJECTED"))


def _mark_reauth(
    connection: Connection,
    tenant_id: UUID,
    site_id: UUID,
    binding_id: UUID,
    operation_id: UUID,
    reason: str,
) -> None:
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.mark_gsc_reauth_required(%s, %s, %s, %s, %s, %s)",
            (tenant_id, site_id, binding_id, uuid4(), reason, operation_id),
        ).fetchone()[0]
    if outcome not in {"restricted", "already_restricted"}:
        raise GscBindingError("GSC_REAUTH_RECORD_UNAVAILABLE")


def _lifecycle(connection, secrets):
    return OAuthBinding(
        connection, secrets, "gsc", GscBindingError, transaction=_clean_transaction, pkce=True
    )
