"""Host-only session cookie and CSRF proof contracts for browser mutations."""

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from signal_core.session_tokens import InvalidOpaqueSessionToken, validate_session_token
from starlette.requests import Request
from starlette.responses import Response

SESSION_COOKIE_NAME = "__Host-signal_session"
IDENTITY_COOKIE_NAME = "__Host-signal_identity"
OIDC_BINDING_COOKIE_NAME = "__Host-signal_oidc_binding"
INVITATION_IDENTITY_COOKIE_NAME = "__Host-signal_invitation_identity"
CSRF_HEADER_NAME = "X-CSRF-Token"
_CSRF_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def runtime_readiness(application, *, tokens, connection_factory, verify) -> bool:
    """Check workload health, the private database and external recovery authority."""
    import asyncio

    if not tokens.healthy:
        return False
    try:

        def check():
            with connection_factory() as connection:
                connection.execute("SELECT 1")

        await asyncio.to_thread(check)
        await application.state.browser_login.recovery_authority.current_generation(verify=verify)
        return True
    except Exception:
        return False


class BrowserRequestRejected(Exception):
    """A browser mutation lacked one indistinguishably required proof."""


class BrowserSecurityUnavailable(Exception):
    """Browser mutation protection has not been configured."""


@dataclass(frozen=True)
class BrowserMutationProof:
    """Validated request possession proof; database authorization is still required."""

    session_token: str = field(repr=False)
    origin: str


@dataclass(frozen=True)
class BrowserLogoutProof:
    """Validated possession proof for either browser session level."""

    session_token: str = field(repr=False)
    session_kind: Literal["identity", "tenant"]
    origin: str


@dataclass(frozen=True)
class BrowserInvitationMutationProof:
    """Validated possession of a short-lived invitation identity proof."""

    identity_proof_token: str = field(repr=False)
    origin: str


@dataclass(frozen=True)
class BrowserSecurity:
    """Issue and verify session-bound CSRF proofs for exact trusted origins."""

    csrf_hmac_key: bytes = field(repr=False)
    allowed_origins: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.csrf_hmac_key, bytes) or len(self.csrf_hmac_key) < 32:
            raise ValueError("The CSRF HMAC key must contain at least 32 bytes.")
        if not self.allowed_origins:
            raise ValueError("At least one exact browser origin is required.")
        normalized = frozenset(_normalize_origin(origin) for origin in self.allowed_origins)
        object.__setattr__(self, "allowed_origins", normalized)

    def issue_csrf_token(self, session_token: object) -> str:
        validated = validate_session_token(session_token)
        digest = hmac.new(
            self.csrf_hmac_key,
            b"signal-browser-csrf-v1\x00" + validated.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def validate_mutation(self, request: Request) -> BrowserMutationProof:
        return self._validate_mutation(request, SESSION_COOKIE_NAME)

    def validate_identity_mutation(self, request: Request) -> BrowserMutationProof:
        """Validate a mutation authorized only to select account context."""
        return self._validate_mutation(request, IDENTITY_COOKIE_NAME)

    def validate_invitation_mutation(self, request: Request) -> BrowserInvitationMutationProof:
        """Validate a mutation without treating the proof as a login session."""
        proof = self._validate_mutation(request, INVITATION_IDENTITY_COOKIE_NAME)
        return BrowserInvitationMutationProof(
            identity_proof_token=proof.session_token,
            origin=proof.origin,
        )

    def validate_logout_mutation(self, request: Request) -> BrowserLogoutProof:
        """Prefer the selected tenant proof while supporting pre-tenant logout."""
        cookie_name, kind = self._logout_target(request)
        proof = self._validate_mutation(request, cookie_name)
        return BrowserLogoutProof(
            session_token=proof.session_token,
            session_kind=kind,
            origin=proof.origin,
        )

    def issue_logout_csrf_token(self, request: Request) -> str:
        """Issue proof for the same strongest session that logout will revoke."""
        cookie_name, _ = self._logout_target(request)
        values = _cookie_values(request, cookie_name)
        try:
            return self.issue_csrf_token(values[0])
        except InvalidOpaqueSessionToken:
            raise BrowserRequestRejected() from None

    @staticmethod
    def _logout_target(request: Request) -> tuple[str, Literal["identity", "tenant"]]:
        tenant_values = _cookie_values(request, SESSION_COOKIE_NAME)
        identity_values = _cookie_values(request, IDENTITY_COOKIE_NAME)
        if len(tenant_values) > 1 or len(identity_values) > 1:
            raise BrowserRequestRejected()
        if tenant_values:
            return SESSION_COOKIE_NAME, "tenant"
        if identity_values:
            return IDENTITY_COOKIE_NAME, "identity"
        raise BrowserRequestRejected()

    def _validate_mutation(self, request: Request, cookie_name: str) -> BrowserMutationProof:
        if request.method.upper() not in _MUTATION_METHODS:
            raise BrowserRequestRejected()

        origins = _header_values(request, "origin")
        if len(origins) != 1:
            raise BrowserRequestRejected()
        try:
            origin = _normalize_origin(origins[0])
        except ValueError:
            raise BrowserRequestRejected() from None
        if origin not in self.allowed_origins:
            raise BrowserRequestRejected()

        fetch_sites = _header_values(request, "sec-fetch-site")
        if len(fetch_sites) > 1 or (
            fetch_sites and fetch_sites[0] not in {"same-origin", "same-site"}
        ):
            raise BrowserRequestRejected()

        session_values = _cookie_values(request, cookie_name)
        csrf_values = _header_values(request, CSRF_HEADER_NAME)
        if len(session_values) != 1 or len(csrf_values) != 1:
            raise BrowserRequestRejected()
        try:
            session_token = validate_session_token(session_values[0])
        except InvalidOpaqueSessionToken:
            raise BrowserRequestRejected() from None

        supplied_csrf = csrf_values[0]
        if _CSRF_TOKEN.fullmatch(supplied_csrf) is None or not hmac.compare_digest(
            supplied_csrf, self.issue_csrf_token(session_token)
        ):
            raise BrowserRequestRejected()
        return BrowserMutationProof(session_token=session_token, origin=origin)


def require_browser_mutation(request: Request) -> BrowserMutationProof:
    """FastAPI dependency for every future cookie-authenticated mutation route."""
    security = getattr(request.app.state, "browser_security", None)
    if not isinstance(security, BrowserSecurity):
        raise BrowserSecurityUnavailable()
    return security.validate_mutation(request)


def require_browser_identity_mutation(request: Request) -> BrowserMutationProof:
    """FastAPI dependency for pre-tenant account-selection mutations."""
    security = getattr(request.app.state, "browser_security", None)
    if not isinstance(security, BrowserSecurity):
        raise BrowserSecurityUnavailable()
    return security.validate_identity_mutation(request)


def require_browser_logout_mutation(request: Request) -> BrowserLogoutProof:
    """FastAPI dependency for logout at either browser session level."""
    security = getattr(request.app.state, "browser_security", None)
    if not isinstance(security, BrowserSecurity):
        raise BrowserSecurityUnavailable()
    return security.validate_logout_mutation(request)


def require_browser_invitation_mutation(request: Request) -> BrowserInvitationMutationProof:
    """FastAPI dependency for proof-bound invitation acceptance."""
    security = getattr(request.app.state, "browser_security", None)
    if not isinstance(security, BrowserSecurity):
        raise BrowserSecurityUnavailable()
    return security.validate_invitation_mutation(request)


def set_session_cookie(response: Response, session_token: object, *, max_age: int) -> None:
    """Set an opaque host-only cookie with the invariants required by its prefix."""
    validated = validate_session_token(session_token)
    _validate_cookie_lifetime(max_age, maximum=86_400)
    _set_host_cookie(response, SESSION_COOKIE_NAME, validated, max_age=max_age)


def set_identity_cookie(response: Response, session_token: object, *, max_age: int) -> None:
    """Set the pre-tenant identity cookie without granting site authority."""
    validated = validate_session_token(session_token)
    _validate_cookie_lifetime(max_age, maximum=86_400)
    _set_host_cookie(response, IDENTITY_COOKIE_NAME, validated, max_age=max_age)


def set_oidc_binding_cookie(response: Response, binding: object, *, max_age: int) -> None:
    """Bind one external callback to the browser that initiated it."""
    validated = validate_session_token(binding)
    _validate_cookie_lifetime(max_age, maximum=600)
    _set_host_cookie(response, OIDC_BINDING_COOKIE_NAME, validated, max_age=max_age)


def set_invitation_identity_cookie(
    response: Response, identity_proof: object, *, max_age: int
) -> None:
    """Set a short-lived proof cookie that grants invitation acceptance only."""
    validated = validate_session_token(identity_proof)
    _validate_cookie_lifetime(max_age, maximum=600)
    _set_host_cookie(response, INVITATION_IDENTITY_COOKIE_NAME, validated, max_age=max_age)


def clear_session_cookie(response: Response) -> None:
    """Expire the host-only cookie without weakening its transport attributes."""
    _clear_host_cookie(response, SESSION_COOKIE_NAME)


def clear_identity_cookie(response: Response) -> None:
    """Expire the pre-tenant identity cookie."""
    _clear_host_cookie(response, IDENTITY_COOKIE_NAME)


def clear_oidc_binding_cookie(response: Response) -> None:
    """Expire the one-login browser binding."""
    _clear_host_cookie(response, OIDC_BINDING_COOKIE_NAME)


def clear_invitation_identity_cookie(response: Response) -> None:
    """Expire the invitation-only verified-identity proof."""
    _clear_host_cookie(response, INVITATION_IDENTITY_COOKIE_NAME)


def exact_cookie(request: Request, name: str) -> str:
    """Return one exact opaque cookie while rejecting ambiguity and bad shape."""
    values = _cookie_values(request, name)
    if len(values) != 1:
        raise BrowserRequestRejected()
    try:
        return validate_session_token(values[0])
    except InvalidOpaqueSessionToken:
        raise BrowserRequestRejected() from None


def _set_host_cookie(response: Response, name: str, value: str, *, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _clear_host_cookie(response: Response, name: str) -> None:
    response.set_cookie(
        name,
        "",
        max_age=0,
        expires="Thu, 01 Jan 1970 00:00:00 GMT",
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _validate_cookie_lifetime(value: object, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise ValueError(f"Cookie max_age must be between 1 and {maximum} seconds.")
    return value


def _header_values(request: Request, name: str) -> list[str]:
    target = name.lower().encode("ascii")
    return [
        value.decode("latin-1").strip()
        for key, value in request.scope.get("headers", ())
        if key.lower() == target
    ]


def _cookie_values(request: Request, name: str) -> list[str]:
    values: list[str] = []
    for cookie_header in _header_values(request, "cookie"):
        for component in cookie_header.split(";"):
            key, separator, value = component.strip().partition("=")
            if key == name:
                values.append(value if separator else "")
    return values


def _normalize_origin(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise ValueError("Browser origins must be nonempty ASCII strings.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Browser origin has an invalid port.") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Browser origin must be an exact HTTP(S) origin.")

    host = parsed.hostname.lower()
    if host != "::1" and _HOST.fullmatch(host) is None:
        raise ValueError("Browser origin host is invalid.")
    if parsed.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise ValueError("Non-loopback browser origins must use HTTPS.")

    rendered_host = f"[{host}]" if ":" in host else host
    default_port = 80 if parsed.scheme == "http" else 443
    rendered_port = "" if port in {None, default_port} else f":{port}"
    return f"{parsed.scheme}://{rendered_host}{rendered_port}"
