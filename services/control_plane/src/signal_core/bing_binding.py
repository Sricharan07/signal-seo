"""Owner-confirmed Bing binding and source-specific import composition."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.bing_protocol import (
    BingObservation,
    BingProtocolError,
    BingSite,
    discover_bing_sites,
    exchange_bing_code,
    import_bing_link_counts,
    import_bing_page_performance,
    import_bing_performance,
    import_bing_url_links,
    new_bing_authorization,
    refresh_bing_token,
)
from signal_core.bing_secrets import BingSecretError, OpenBaoBingSecrets
from signal_core.connector_framework import OAuthBinding, refresh_oauth, state_digest
from signal_core.database import _clean_transaction
from signal_core.session_tokens import session_token_hasher
from signal_core.shared_egress import SharedEgressProvider


class BingBindingError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class BingAuthorizationStarted:
    attempt_id: UUID
    authorization_url: str
    expires_in_seconds: int = 300


@dataclass(frozen=True)
class BingSiteSelection:
    attempt_id: UUID
    sites: tuple[BingSite, ...]


@dataclass(frozen=True)
class BingBinding:
    binding_id: UUID
    site_url: str


@dataclass(frozen=True)
class BingImportGeneration:
    generation_id: UUID
    site_url: str
    kind: str
    observation: BingObservation


async def begin_bing_authorization(
    connection: Connection,
    secrets_store: OpenBaoBingSecrets,
    *,
    session_token: str,
    recovery_generation: str,
    site_id: UUID,
    redirect_uri: str,
    openbao_options: dict | None = None,
) -> BingAuthorizationStarted:
    credentials = await secrets_store.client_credentials(**(openbao_options or {}))
    authorization = new_bing_authorization(credentials.client_id, redirect_uri)
    attempt_id = uuid4()
    await _lifecycle(connection, secrets_store).begin(
        (_session_hash(session_token), recovery_generation, site_id),
        authorization,
        redirect_uri,
        attempt_id,
    )
    return BingAuthorizationStarted(attempt_id, authorization.url)


async def complete_bing_authorization(
    connection: Connection,
    secrets_store: OpenBaoBingSecrets,
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
) -> BingSiteSelection:
    state_digest(state, BingBindingError("BING_STATE_REJECTED"))
    args = (_session_hash(session_token), recovery_generation, site_id)

    def validate(tokens, sites):
        if not any(site.eligible for site in sites) or tokens.refresh_token is None:
            raise BingBindingError("BING_NO_ELIGIBLE_SITE")
        return sites

    sites = await _lifecycle(connection, secrets_store).complete(
        args,
        attempt_id,
        state,
        redirect_uri,
        lambda credentials, verifier: exchange_bing_code(
            egress,
            credentials.client_id,
            credentials.client_secret,
            code,
            redirect_uri,
            token_operation_id,
        ),
        lambda tokens, origin: discover_bing_sites(
            egress, tokens.access_token, origin, discovery_operation_id
        ),
        lambda items: Jsonb([{"url": site.url, "eligible": site.eligible} for site in items]),
        validate,
        **(openbao_options or {}),
    )
    return BingSiteSelection(attempt_id, sites)


def confirm_bing_binding(
    connection: Connection,
    *,
    session_token: str,
    recovery_generation: str,
    site_id: UUID,
    attempt_id: UUID,
    site_url: str,
) -> BingBinding:
    binding = _lifecycle(connection, None).confirm(
        (_session_hash(session_token), recovery_generation, site_id), attempt_id, site_url
    )
    return BingBinding(binding, site_url)


async def revoke_bing_binding(
    connection: Connection,
    secrets_store: OpenBaoBingSecrets,
    *,
    session_token: str,
    recovery_generation: str,
    site_id: UUID,
    binding_id: UUID,
    openbao_options: dict | None = None,
) -> None:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT outcome, secret_reference FROM control.revoke_bing_binding(%s, %s, %s, %s, %s)",
            (_session_hash(session_token), recovery_generation, site_id, binding_id, uuid4()),
        ).fetchone()
    if row is None or row[0] != "revoked":
        raise BingBindingError("BING_REVOKE_DENIED")
    await secrets_store.destroy_refresh_token(row[1], **(openbao_options or {}))


async def import_bing_observation(
    connection: Connection,
    secrets_store: OpenBaoBingSecrets,
    egress: SharedEgressProvider,
    *,
    tenant_id: UUID,
    site_id: UUID,
    kind: str,
    refresh_operation_id: UUID,
    import_operation_id: UUID,
    target_url: str | None = None,
) -> BingImportGeneration:
    if kind not in {
        "performance",
        "page_performance",
        "own_site_inbound_link_counts",
        "own_site_inbound_link_details",
    }:
        raise BingBindingError("BING_KIND_REJECTED")
    if (kind == "own_site_inbound_link_details") != (target_url is not None):
        raise BingBindingError("BING_TARGET_REJECTED")
    if kind == "page_performance" and (
        not isinstance(egress, SharedEgressProvider)
        or egress.purpose != "connector"
        or getattr(getattr(egress, "run", None), "tenant_id", None) != tenant_id
        or getattr(getattr(egress, "run", None), "site_id", None) != site_id
    ):
        raise BingBindingError("BING_SCOPE_REJECTED")
    with _clean_transaction(connection):
        binding = connection.execute(
            "SELECT binding_id, property_resource_name, secret_reference, origin "
            "FROM control.current_bing_binding(%s, %s)",
            (tenant_id, site_id),
        ).fetchone()
    if binding is None:
        raise BingBindingError("BING_BINDING_UNAVAILABLE")
    lock_key = int.from_bytes(hashlib.sha256(binding[0].bytes).digest()[:8], "big", signed=True)
    locked = connection.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,)).fetchone()[0]
    if not locked:
        raise BingBindingError("BING_REFRESH_BUSY")
    try:
        with _clean_transaction(connection):
            current = connection.execute(
                "SELECT binding_id FROM control.current_bing_binding(%s, %s)",
                (tenant_id, site_id),
            ).fetchone()
        if current is None or current[0] != binding[0]:
            raise BingBindingError("BING_BINDING_UNAVAILABLE")
        tokens = await refresh_oauth(
            secrets_store,
            binding[2],
            lambda credentials, refresh: refresh_bing_token(
                egress,
                credentials.client_id,
                credentials.client_secret,
                refresh,
                refresh_operation_id,
            ),
            lambda reason: _restrict(
                connection, tenant_id, site_id, binding[0], refresh_operation_id, reason
            ),
            protocol_error=BingProtocolError,
            reauth_codes={
                "BING_REAUTH_REQUIRED": "provider_revoked",
                "BING_REDUCED_SCOPE": "reduced_scope",
            },
            secret_error=BingSecretError,
            rotation_error=BingBindingError("BING_ROTATION_UNPERSISTED"),
            options={},
        )

    finally:
        connection.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
    try:
        if kind == "performance":
            observation = import_bing_performance(
                egress, tokens.access_token, binding[1], import_operation_id
            )
        elif kind == "page_performance":
            observation = import_bing_page_performance(
                egress, tokens.access_token, binding[1], import_operation_id
            )
        elif kind == "own_site_inbound_link_counts":
            observation = import_bing_link_counts(
                egress, tokens.access_token, binding[1], import_operation_id
            )
        else:
            observation = import_bing_url_links(
                egress, tokens.access_token, binding[1], target_url, import_operation_id
            )
    except BingProtocolError as error:
        if error.code == "BING_REAUTH_REQUIRED":
            _restrict(
                connection, tenant_id, site_id, binding[0], import_operation_id, "provider_revoked"
            )
        raise
    generation_id = uuid4()
    recorder = (
        "record_bing_page_import_generation"
        if kind == "page_performance"
        else "record_bing_import_generation"
    )
    with _clean_transaction(connection):
        outcome = connection.execute(
            f"SELECT control.{recorder}(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                site_id,
                generation_id,
                binding[0],
                binding[1],
                kind,
                Jsonb(list(observation.rows)),
                Jsonb(observation.coverage),
                observation.response_sha256,
                import_operation_id,
            ),
        ).fetchone()[0]
    if outcome != "recorded":
        raise BingBindingError("BING_IMPORT_REJECTED")
    return BingImportGeneration(generation_id, binding[1], kind, observation)


def read_bing_page_performance(
    connection: Connection, *, tenant_id: UUID, site_id: UUID
) -> BingImportGeneration | None:
    """Internal ingest read port; absent generations never become zero metrics."""
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT generation_id, site_url, rows, coverage, response_sha256 "
            "FROM control.read_bing_page_performance(%s, %s)",
            (tenant_id, site_id),
        ).fetchone()
    if row is None:
        return None
    return BingImportGeneration(
        row[0], row[1], "page_performance", BingObservation(tuple(row[2]), row[3], row[4])
    )


_session_hash = session_token_hasher(lambda: BingBindingError("BING_SESSION_REJECTED"))


def _restrict(
    connection: Connection,
    tenant_id: UUID,
    site_id: UUID,
    binding_id: UUID,
    operation_id: UUID,
    reason: str,
) -> None:
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.mark_bing_reauth_required(%s, %s, %s, %s, %s, %s)",
            (tenant_id, site_id, binding_id, uuid4(), reason, operation_id),
        ).fetchone()[0]
    if outcome not in {"restricted", "already_restricted"}:
        raise BingBindingError("BING_REAUTH_RECORD_UNAVAILABLE")


def _lifecycle(connection, secrets):
    return OAuthBinding(
        connection, secrets, "bing", BingBindingError, transaction=_clean_transaction, pkce=False
    )
