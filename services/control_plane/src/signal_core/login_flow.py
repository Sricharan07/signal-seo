"""Compose one fail-closed OIDC login without exposing a public HTTP route."""

import re
import secrets
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from uuid import UUID, uuid4

import httpx2
from anyio import to_thread
from psycopg import Connection
from psycopg import Error as PsycopgError

from signal_core.invitation_identity_proofs import (
    InvitationIdentityProofDenied,
    IssuedInvitationIdentityProof,
    issue_invitation_identity_proof,
)
from signal_core.oidc_login import (
    ConsumedOidcLoginAttempt,
    InvalidOidcLoginAttempt,
    OidcClientRegistration,
    OidcLoginAuditError,
    OidcLoginStateConflict,
    consume_oidc_login_attempt,
    create_oidc_login_attempt,
    read_oidc_login_purpose,
    record_consumed_login_failure,
    validate_oidc_attempt_ttl,
    validate_oidc_login_purpose,
    validate_oidc_return_path,
)
from signal_core.oidc_protocol import (
    OidcProtocolError,
    OidcTokenResponse,
    VerifiedOidcIdentity,
    create_authorization_request,
    discover_keycloak,
    exchange_authorization_code,
    fetch_jwks,
    validate_authorization_code,
    validate_id_token,
)
from signal_core.pkce_secrets import (
    OpenBaoPkceClient,
    PkceSecretError,
    PkceSecretUnavailable,
)
from signal_core.recovery_authority import (
    OpenBaoRecoveryAuthority,
    RecoveryAuthorityError,
)
from signal_core.session_issuance import (
    DEFAULT_SESSION_POLICY,
    IssuedIdentitySession,
    SessionIssuanceDenied,
    SessionPolicy,
    issue_identity_session,
)

_OPAQUE_VALUE = re.compile(r"[A-Za-z0-9_-]{43}")
_PKCE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")


class LoginFlowError(Exception):
    """A composed login stage failed without retaining sensitive exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class InitiatedOidcLogin:
    attempt_id: UUID
    authorization_url: str = field(repr=False)
    browser_binding: str = field(repr=False)


@dataclass(frozen=True)
class CompletedOidcLogin:
    identity_session: IssuedIdentitySession = field(repr=False)
    return_path: str


@dataclass(frozen=True)
class CompletedInvitationIdentityVerification:
    identity_proof: IssuedInvitationIdentityProof = field(repr=False)
    return_path: str


def _opaque_token() -> str:
    return secrets.token_urlsafe(32)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


async def initiate_oidc_login(
    connection: Connection,
    *,
    registration: OidcClientRegistration,
    pkce_writer: OpenBaoPkceClient,
    return_path: object,
    purpose: object = "login",
    ttl_seconds: int = 300,
    oidc_transport: httpx2.AsyncBaseTransport | None = None,
    oidc_verify: ssl.SSLContext | bool = True,
    pkce_transport: httpx2.AsyncBaseTransport | None = None,
    pkce_verify: ssl.SSLContext | bool = True,
    opaque_token_factory: Callable[[], str] = _opaque_token,
    verifier_factory: Callable[[], str] = _pkce_verifier,
    attempt_id_factory: Callable[[], UUID] = uuid4,
) -> InitiatedOidcLogin:
    """Return a provider request only after its one-login verifier is durable."""
    if not isinstance(registration, OidcClientRegistration) or not isinstance(
        pkce_writer, OpenBaoPkceClient
    ):
        raise ValueError("Validated OIDC and OpenBao configurations are required.")
    if not all(callable(factory) for factory in (opaque_token_factory, verifier_factory)) or not (
        callable(attempt_id_factory)
    ):
        raise ValueError("Login entropy factories must be callable.")
    validated_return_path = validate_oidc_return_path(return_path)
    validated_purpose = validate_oidc_login_purpose(purpose)
    validated_ttl = validate_oidc_attempt_ttl(ttl_seconds)

    try:
        attempt_id = attempt_id_factory()
        if not isinstance(attempt_id, UUID) or attempt_id.version != 4:
            raise ValueError
        state, nonce, browser_binding = tuple(opaque_token_factory() for _ in range(3))
        code_verifier = verifier_factory()
        if (
            any(
                not isinstance(value, str) or _OPAQUE_VALUE.fullmatch(value) is None
                for value in (state, nonce, browser_binding)
            )
            or len({state, nonce, browser_binding}) != 3
            or not isinstance(code_verifier, str)
            or _PKCE_VERIFIER.fullmatch(code_verifier) is None
        ):
            raise ValueError
    except (RuntimeError, StopIteration, TypeError, ValueError):
        raise LoginFlowError("LOGIN_START_ENTROPY_FAILED") from None

    try:
        metadata = await discover_keycloak(
            registration,
            transport=oidc_transport,
            verify=oidc_verify,
        )
        authorization_url = await create_authorization_request(
            registration,
            metadata,
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
        )
    except OidcProtocolError:
        raise LoginFlowError("LOGIN_START_PROVIDER_FAILED") from None

    try:
        secret_reference = await pkce_writer.store_verifier(
            attempt_id=attempt_id,
            code_verifier=code_verifier,
            transport=pkce_transport,
            verify=pkce_verify,
        )
    except (PkceSecretError, ValueError):
        raise LoginFlowError("LOGIN_START_SECRET_FAILED") from None
    expected_reference = f"secret://oidc-login/{attempt_id}/1"
    if secret_reference != expected_reference:
        raise LoginFlowError("LOGIN_START_SECRET_FAILED")

    try:
        persisted_id = await to_thread.run_sync(
            partial(
                create_oidc_login_attempt,
                connection,
                attempt_id=attempt_id,
                state=state,
                nonce=nonce,
                browser_binding=browser_binding,
                registration=registration,
                pkce_secret_reference=secret_reference,
                return_path=validated_return_path,
                purpose=validated_purpose,
                ttl_seconds=validated_ttl,
            )
        )
    except (OidcLoginStateConflict, PsycopgError, ValueError):
        # The unreachable secret is bounded by the OpenBao mount expiry. The
        # initiation credential intentionally cannot read or delete it.
        raise LoginFlowError("LOGIN_START_PERSISTENCE_FAILED") from None
    if persisted_id != attempt_id:
        raise LoginFlowError("LOGIN_START_PERSISTENCE_FAILED")
    return InitiatedOidcLogin(
        attempt_id=attempt_id,
        authorization_url=authorization_url,
        browser_binding=browser_binding,
    )


async def complete_oidc_login(
    connection: Connection,
    *,
    state: object,
    browser_binding: object,
    code: object,
    pkce_consumer: OpenBaoPkceClient,
    recovery_authority: OpenBaoRecoveryAuthority,
    policy: SessionPolicy = DEFAULT_SESSION_POLICY,
    now: datetime | None = None,
    oidc_transport: httpx2.AsyncBaseTransport | None = None,
    oidc_verify: ssl.SSLContext | bool = True,
    pkce_transport: httpx2.AsyncBaseTransport | None = None,
    pkce_verify: ssl.SSLContext | bool = True,
    recovery_transport: httpx2.AsyncBaseTransport | None = None,
    recovery_verify: ssl.SSLContext | bool = True,
    session_token_factory: Callable[[], str] | None = None,
    invitation_proof_token_factory: Callable[[], str] | None = None,
    invitation_proof_id_factory: Callable[[], UUID] | None = None,
    verified_assertion_observer: Callable[
        [ConsumedOidcLoginAttempt, OidcTokenResponse, VerifiedOidcIdentity], None
    ]
    | None = None,
) -> CompletedOidcLogin | CompletedInvitationIdentityVerification:
    """Consume every proof once, then issue only the artifact bound to its purpose."""
    if (
        not isinstance(pkce_consumer, OpenBaoPkceClient)
        or not isinstance(recovery_authority, OpenBaoRecoveryAuthority)
        or not isinstance(policy, SessionPolicy)
    ):
        raise ValueError("Validated OpenBao and session-policy configurations are required.")
    optional_factories = (
        session_token_factory,
        invitation_proof_token_factory,
        invitation_proof_id_factory,
        verified_assertion_observer,
    )
    if any(factory is not None and not callable(factory) for factory in optional_factories):
        raise ValueError("Login completion factories must be callable.")
    try:
        current = _normalize_time(now)
    except ValueError:
        raise LoginFlowError("LOGIN_CALLBACK_CONFIGURATION_REJECTED") from None
    try:
        validated_code = validate_authorization_code(code)
    except OidcProtocolError:
        raise LoginFlowError("LOGIN_CALLBACK_REJECTED") from None

    try:
        purpose = await to_thread.run_sync(
            partial(
                read_oidc_login_purpose,
                connection,
                state=state,
                browser_binding=browser_binding,
            )
        )
    except (InvalidOidcLoginAttempt, PsycopgError, ValueError):
        raise LoginFlowError("LOGIN_CALLBACK_REJECTED") from None

    generation = None
    if purpose == "login":
        try:
            generation = await recovery_authority.current_generation(
                transport=recovery_transport,
                verify=recovery_verify,
            )
        except RecoveryAuthorityError:
            raise LoginFlowError("LOGIN_RECOVERY_AUTHORITY_FAILED") from None

    try:
        attempt = await to_thread.run_sync(
            partial(
                consume_oidc_login_attempt,
                connection,
                state=state,
                browser_binding=browser_binding,
            )
        )
    except (InvalidOidcLoginAttempt, PsycopgError, ValueError):
        raise LoginFlowError("LOGIN_CALLBACK_REJECTED") from None
    if attempt.purpose != purpose:
        raise LoginFlowError("LOGIN_CALLBACK_REJECTED")

    try:
        metadata = await discover_keycloak(
            attempt.registration,
            transport=oidc_transport,
            verify=oidc_verify,
        )
        jwks = await fetch_jwks(
            attempt.registration,
            metadata,
            transport=oidc_transport,
            verify=oidc_verify,
        )
    except OidcProtocolError:
        await _record_consumed_failure(
            connection,
            attempt=attempt,
            state=state,
            browser_binding=browser_binding,
            reason="provider_configuration_failed",
        )
        raise LoginFlowError("LOGIN_CALLBACK_PROVIDER_FAILED") from None

    try:
        code_verifier = await pkce_consumer.consume_verifier(
            secret_reference=attempt.pkce_secret_reference,
            transport=pkce_transport,
            verify=pkce_verify,
        )
    except PkceSecretUnavailable:
        await _record_consumed_failure(
            connection,
            attempt=attempt,
            state=state,
            browser_binding=browser_binding,
            reason="pkce_unavailable",
        )
        raise LoginFlowError("LOGIN_CALLBACK_REJECTED") from None
    except PkceSecretError:
        await _record_consumed_failure(
            connection,
            attempt=attempt,
            state=state,
            browser_binding=browser_binding,
            reason="pkce_consume_failed",
        )
        raise LoginFlowError("LOGIN_CALLBACK_SECRET_FAILED") from None

    try:
        token_response = await exchange_authorization_code(
            attempt.registration,
            metadata,
            code=validated_code,
            code_verifier=code_verifier,
            transport=oidc_transport,
            verify=oidc_verify,
        )
        identity = validate_id_token(
            token_response,
            attempt=attempt,
            jwks=jwks,
            now=int(current.timestamp()),
        )
    except OidcProtocolError:
        await _record_consumed_failure(
            connection,
            attempt=attempt,
            state=state,
            browser_binding=browser_binding,
            reason="provider_assertion_failed",
        )
        raise LoginFlowError("LOGIN_CALLBACK_PROVIDER_FAILED") from None

    if purpose == "invitation_acceptance":
        proof_arguments = {"identity": identity, "now": current}
        if invitation_proof_token_factory is not None:
            proof_arguments["token_factory"] = invitation_proof_token_factory
        if invitation_proof_id_factory is not None:
            proof_arguments["proof_id_factory"] = invitation_proof_id_factory
        try:
            proof = await to_thread.run_sync(
                partial(issue_invitation_identity_proof, connection, **proof_arguments)
            )
        except InvitationIdentityProofDenied:
            await _record_consumed_failure(
                connection,
                attempt=attempt,
                state=state,
                browser_binding=browser_binding,
                reason="invitation_identity_not_verified",
            )
            raise LoginFlowError("LOGIN_CALLBACK_REJECTED") from None
        except (PsycopgError, RuntimeError, TypeError, ValueError):
            await _record_consumed_failure(
                connection,
                attempt=attempt,
                state=state,
                browser_binding=browser_binding,
                reason="invitation_proof_persistence_failed",
            )
            raise LoginFlowError("LOGIN_CALLBACK_PROOF_FAILED") from None
        return CompletedInvitationIdentityVerification(
            identity_proof=proof,
            return_path=attempt.return_path,
        )

    if generation is None:
        raise LoginFlowError("LOGIN_CALLBACK_CONFIGURATION_REJECTED")
    if verified_assertion_observer is not None:
        try:
            await to_thread.run_sync(
                partial(verified_assertion_observer, attempt, token_response, identity)
            )
        except Exception:
            await _record_consumed_failure(
                connection,
                attempt=attempt,
                state=state,
                browser_binding=browser_binding,
                reason="identity_not_authorized",
            )
            raise LoginFlowError("LOGIN_CALLBACK_REJECTED") from None
    session_arguments = {
        "identity": identity,
        "current_recovery_generation": generation.value,
        "policy": policy,
        "now": current,
    }
    if session_token_factory is not None:
        session_arguments["token_factory"] = session_token_factory
    try:
        session = await to_thread.run_sync(
            partial(issue_identity_session, connection, **session_arguments)
        )
    except SessionIssuanceDenied:
        await _record_consumed_failure(
            connection,
            attempt=attempt,
            state=state,
            browser_binding=browser_binding,
            reason="identity_not_authorized",
        )
        raise LoginFlowError("LOGIN_CALLBACK_REJECTED") from None
    except (PsycopgError, RuntimeError, ValueError, TypeError):
        await _record_consumed_failure(
            connection,
            attempt=attempt,
            state=state,
            browser_binding=browser_binding,
            reason="session_persistence_failed",
        )
        raise LoginFlowError("LOGIN_CALLBACK_SESSION_FAILED") from None
    return CompletedOidcLogin(identity_session=session, return_path=attempt.return_path)


def _normalize_time(value: datetime | None) -> datetime:
    current = value if value is not None else datetime.now(UTC)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Login completion requires an aware current time.")
    return current.astimezone(UTC)


async def _record_consumed_failure(
    connection: Connection,
    *,
    attempt: ConsumedOidcLoginAttempt,
    state: object,
    browser_binding: object,
    reason: str,
) -> None:
    try:
        await to_thread.run_sync(
            partial(
                record_consumed_login_failure,
                connection,
                attempt=attempt,
                state=state,
                browser_binding=browser_binding,
                reason=reason,
            )
        )
    except OidcLoginAuditError:
        raise LoginFlowError("LOGIN_CALLBACK_AUDIT_FAILED") from None
