"""Fail-closed Keycloak OIDC authorization-code protocol boundary."""

import base64
import hashlib
import hmac
import json
import re
import ssl
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from signal_core.identity_conditions import normalize_ascii_mailbox
from signal_core.oidc_login import (
    ConsumedOidcLoginAttempt,
    InvalidOidcLoginAttempt,
    OidcClientRegistration,
)

_OPAQUE_VALUE = re.compile(r"[A-Za-z0-9_-]{43}")
_PKCE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_AUTHORIZATION_CODE = re.compile(r"[\x21-\x7e]{1,4096}")
_MAX_DOCUMENT_BYTES = 128 * 1024
_MAX_TOKEN_BYTES = 16 * 1024
_MAX_TOKEN_AGE_SECONDS = 600
_CLOCK_SKEW_SECONDS = 30
_ALLOWED_ID_TOKEN_ALGORITHMS = ("RS256",)
_PRIVATE_JWK_PARAMETERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})


class OidcProtocolError(Exception):
    """A provider response failed a fixed, safe OIDC protocol check."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ResponseTooLarge(httpx2.TransportError):
    pass


class _BoundedByteStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream, maximum: int) -> None:
        self._stream = stream
        self._maximum = maximum

    async def __aiter__(self) -> AsyncIterator[bytes]:
        observed = 0
        async for chunk in self._stream:
            observed += len(chunk)
            if observed > self._maximum:
                raise _ResponseTooLarge("OIDC response exceeded its fixed limit.")
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class _BoundedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, transport: httpx2.AsyncBaseTransport, maximum: int) -> None:
        self._transport = transport
        self._maximum = maximum

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await self._transport.handle_async_request(request)
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._maximum:
                    await response.aclose()
                    raise _ResponseTooLarge("OIDC response exceeded its fixed limit.")
            except ValueError:
                await response.aclose()
                raise _ResponseTooLarge("OIDC response length was invalid.") from None
        response.stream = _BoundedByteStream(response.stream, self._maximum)
        return response

    async def aclose(self) -> None:
        await self._transport.aclose()


@dataclass(frozen=True)
class KeycloakMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str

    @classmethod
    def validate(
        cls, registration: OidcClientRegistration, document: Mapping[str, Any]
    ) -> "KeycloakMetadata":
        """Validate discovery without accepting provider-selected network destinations."""
        if registration.issuer.endswith("/"):
            raise OidcProtocolError("OIDC_METADATA_REJECTED")
        prefix = f"{registration.issuer}/protocol/openid-connect"
        exact_values = {
            "issuer": registration.issuer,
            "authorization_endpoint": f"{prefix}/auth",
            "token_endpoint": f"{prefix}/token",
            "jwks_uri": f"{prefix}/certs",
        }
        if any(document.get(key) != value for key, value in exact_values.items()):
            raise OidcProtocolError("OIDC_METADATA_REJECTED")
        if "code" not in _string_list(document.get("response_types_supported")):
            raise OidcProtocolError("OIDC_METADATA_REJECTED")
        if "S256" not in _string_list(document.get("code_challenge_methods_supported")):
            raise OidcProtocolError("OIDC_METADATA_REJECTED")
        if "RS256" not in _string_list(document.get("id_token_signing_alg_values_supported")):
            raise OidcProtocolError("OIDC_METADATA_REJECTED")
        return cls(**exact_values)


@dataclass(frozen=True)
class OidcTokenResponse:
    id_token: str = field(repr=False)
    access_token: str = field(repr=False)
    expires_in: int

    def __post_init__(self) -> None:
        _bounded_token(self.id_token)
        _bounded_token(self.access_token)
        _validate_expires_in(self.expires_in)


@dataclass(frozen=True)
class VerifiedOidcIdentity:
    issuer: str
    subject: str
    client_id: str
    issued_at: int
    expires_at: int
    auth_time: int | None
    provider_session_id: str | None
    authentication_context: str | None
    verified_email: str | None = field(default=None, repr=False)
    authentication_methods: frozenset[str] = frozenset()


async def discover_keycloak(
    registration: OidcClientRegistration,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    verify: ssl.SSLContext | bool = True,
) -> KeycloakMetadata:
    url = f"{registration.issuer.rstrip('/')}/.well-known/openid-configuration"
    document = await _fetch_json(url, transport=transport, verify=verify)
    return KeycloakMetadata.validate(registration, document)


async def fetch_jwks(
    registration: OidcClientRegistration,
    metadata: KeycloakMetadata,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    verify: ssl.SSLContext | bool = True,
) -> dict[str, Any]:
    _validate_metadata_registration(metadata, registration)
    document = await _fetch_json(metadata.jwks_uri, transport=transport, verify=verify)
    return _validate_public_jwks(document)


async def create_authorization_request(
    registration: OidcClientRegistration,
    metadata: KeycloakMetadata,
    *,
    state: object,
    nonce: object,
    code_verifier: object,
) -> str:
    validated_state = _validate_opaque_value(state)
    validated_nonce = _validate_opaque_value(nonce)
    verifier = _validate_pkce_verifier(code_verifier)
    _validate_metadata_registration(metadata, registration)
    client = AsyncOAuth2Client(
        client_id=registration.client_id,
        redirect_uri=registration.redirect_uri,
        scope="openid email",
        code_challenge_method="S256",
        token_endpoint_auth_method="none",
        follow_redirects=False,
        trust_env=False,
    )
    try:
        url, returned_state = client.create_authorization_url(
            metadata.authorization_endpoint,
            state=validated_state,
            nonce=validated_nonce,
            code_verifier=verifier,
            max_age=0,
        )
    finally:
        await client.aclose()
    if returned_state != validated_state:
        raise OidcProtocolError("OIDC_AUTHORIZATION_REQUEST_REJECTED")
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    required = {
        "client_id": [registration.client_id],
        "redirect_uri": [registration.redirect_uri],
        "response_type": ["code"],
        "scope": ["openid email"],
        "state": [validated_state],
        "nonce": [validated_nonce],
        "code_challenge_method": ["S256"],
        "code_challenge": [_pkce_challenge(verifier)],
        "max_age": ["0"],
    }
    if urlsplit(url)._replace(query="", fragment="").geturl() != metadata.authorization_endpoint:
        raise OidcProtocolError("OIDC_AUTHORIZATION_REQUEST_REJECTED")
    if set(query) != set(required) or any(
        query.get(key) != value for key, value in required.items()
    ):
        raise OidcProtocolError("OIDC_AUTHORIZATION_REQUEST_REJECTED")
    return url


async def exchange_authorization_code(
    registration: OidcClientRegistration,
    metadata: KeycloakMetadata,
    *,
    code: object,
    code_verifier: object,
    transport: httpx2.AsyncBaseTransport | None = None,
    verify: ssl.SSLContext | bool = True,
) -> OidcTokenResponse:
    validated_code = validate_authorization_code(code)
    verifier = _validate_pkce_verifier(code_verifier)
    _validate_metadata_registration(metadata, registration)
    tls_verifier = _trusted_tls_verifier(verify)
    inner_transport = transport or httpx2.AsyncHTTPTransport(verify=tls_verifier)
    client = AsyncOAuth2Client(
        client_id=registration.client_id,
        redirect_uri=registration.redirect_uri,
        scope="openid email",
        code_challenge_method="S256",
        token_endpoint_auth_method="none",
        timeout=httpx2.Timeout(5.0),
        follow_redirects=False,
        transport=_BoundedTransport(inner_transport, _MAX_DOCUMENT_BYTES),
        trust_env=False,
    )
    try:
        token = await client.fetch_token(
            metadata.token_endpoint,
            grant_type="authorization_code",
            code=validated_code,
            code_verifier=verifier,
        )
    except (AuthlibBaseError, httpx2.HTTPError, ValueError, TypeError):
        raise OidcProtocolError("OIDC_CODE_EXCHANGE_FAILED") from None
    finally:
        await client.aclose()
    try:
        id_token = _bounded_token(token.get("id_token"))
        access_token = _bounded_token(token.get("access_token"))
        expires_in = _validate_expires_in(token.get("expires_in"))
        if token.get("token_type", "").lower() != "bearer":
            raise ValueError
    except (AttributeError, ValueError):
        raise OidcProtocolError("OIDC_TOKEN_RESPONSE_REJECTED") from None
    return OidcTokenResponse(
        id_token=id_token,
        access_token=access_token,
        expires_in=expires_in,
    )


def validate_id_token(
    token_response: OidcTokenResponse,
    *,
    attempt: ConsumedOidcLoginAttempt,
    jwks: Mapping[str, Any],
    now: int | None = None,
) -> VerifiedOidcIdentity:
    """Validate a Keycloak ID token before identity or session creation."""
    if (
        not isinstance(token_response, OidcTokenResponse)
        or not isinstance(attempt, ConsumedOidcLoginAttempt)
        or not isinstance(attempt.registration, OidcClientRegistration)
        or not isinstance(attempt.nonce_hash, bytes)
        or len(attempt.nonce_hash) != 32
    ):
        raise OidcProtocolError("OIDC_ID_TOKEN_REJECTED")
    try:
        keys = KeySet.import_key_set(_validate_public_jwks(jwks))
        token = jwt.decode(
            token_response.id_token,
            keys,
            algorithms=_ALLOWED_ID_TOKEN_ALGORITHMS,
        )
        header = token.header
        claims = token.claims
        if header.get("alg") != "RS256" or header.get("typ") not in {None, "JWT"}:
            raise ValueError
        kid = header.get("kid")
        if not isinstance(kid, str) or not 1 <= len(kid) <= 128:
            raise ValueError
        for claim_name in ("exp", "iat"):
            value = claims.get(claim_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError
        checked_now = now if now is not None else time.time_ns() // 1_000_000_000
        if isinstance(checked_now, bool) or not isinstance(checked_now, int):
            raise ValueError
        registry = JWTClaimsRegistry(
            now=checked_now,
            leeway=_CLOCK_SKEW_SECONDS,
            iss={"essential": True, "value": attempt.registration.issuer},
            sub={"essential": True},
            aud={"essential": True, "value": attempt.registration.client_id},
            exp={"essential": True},
            iat={"essential": True},
            nonce={"essential": True},
        )
        registry.validate(claims)
        subject = claims["sub"]
        if (
            not isinstance(subject, str)
            or not 1 <= len(subject) <= 255
            or any(ord(character) < 0x20 for character in subject)
        ):
            raise ValueError
        issued_at = claims["iat"]
        expires_at = claims["exp"]
        if (
            issued_at < checked_now - _MAX_TOKEN_AGE_SECONDS - _CLOCK_SKEW_SECONDS
            or expires_at > issued_at + _MAX_TOKEN_AGE_SECONDS
            or expires_at <= issued_at
        ):
            raise ValueError
        audience = claims["aud"]
        authorized_party = claims.get("azp")
        if isinstance(audience, list) and len(audience) > 1 and authorized_party is None:
            raise ValueError
        if authorized_party is not None and authorized_party != attempt.registration.client_id:
            raise ValueError
        attempt.validate_nonce(claims["nonce"])
        if "at_hash" in claims and not _valid_access_token_hash(
            claims["at_hash"], token_response.access_token
        ):
            raise ValueError
        auth_time = _optional_numeric_date(claims.get("auth_time"), checked_now)
        provider_session_id = _optional_bounded_string(claims.get("sid"), 255)
        authentication_context = _optional_bounded_string(claims.get("acr"), 255)
        verified_email = _verified_email_claim(claims)
        authentication_methods = _authentication_methods(claims.get("amr"))
    except (JoseError, InvalidOidcLoginAttempt, KeyError, TypeError, ValueError):
        raise OidcProtocolError("OIDC_ID_TOKEN_REJECTED") from None
    return VerifiedOidcIdentity(
        issuer=attempt.registration.issuer,
        subject=subject,
        client_id=attempt.registration.client_id,
        issued_at=issued_at,
        expires_at=expires_at,
        auth_time=auth_time,
        provider_session_id=provider_session_id,
        authentication_context=authentication_context,
        verified_email=verified_email,
        authentication_methods=authentication_methods,
    )


def _authentication_methods(value: object) -> frozenset[str]:
    if value is None:
        return frozenset()
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 16
        or any(
            not isinstance(method, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", method) is None
            for method in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValueError("Invalid authentication method claim.")
    return frozenset(value)


async def _fetch_json(
    url: str,
    *,
    transport: httpx2.AsyncBaseTransport | None,
    verify: ssl.SSLContext | bool,
) -> dict[str, Any]:
    try:
        async with httpx2.AsyncClient(
            timeout=httpx2.Timeout(5.0),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
            verify=_trusted_tls_verifier(verify),
        ) as client:
            async with client.stream(
                "GET", url, headers={"Accept": "application/json"}
            ) as response:
                response.raise_for_status()
                media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if media_type not in {"application/json", "application/jwk-set+json"}:
                    raise ValueError
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_DOCUMENT_BYTES:
                        raise ValueError
        document = json.loads(body)
        if not isinstance(document, dict):
            raise ValueError
        return document
    except (httpx2.HTTPError, UnicodeError, json.JSONDecodeError, ValueError):
        raise OidcProtocolError("OIDC_PROVIDER_DOCUMENT_REJECTED") from None


def _validate_public_jwks(document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, Mapping) or set(document) != {"keys"}:
        raise OidcProtocolError("OIDC_JWKS_REJECTED")
    keys = document.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 10:
        raise OidcProtocolError("OIDC_JWKS_REJECTED")
    validated = []
    seen_kids = set()
    for key in keys:
        if not isinstance(key, dict) or _PRIVATE_JWK_PARAMETERS.intersection(key):
            raise OidcProtocolError("OIDC_JWKS_REJECTED")
        if (
            key.get("kty") != "RSA"
            or key.get("use", "sig") != "sig"
            or key.get("alg", "RS256") != "RS256"
        ):
            continue
        kid = key.get("kid")
        if not isinstance(kid, str) or not 1 <= len(kid) <= 128 or kid in seen_kids:
            raise OidcProtocolError("OIDC_JWKS_REJECTED")
        key_operations = key.get("key_ops", ["verify"])
        if not isinstance(key_operations, list) or "verify" not in key_operations:
            raise OidcProtocolError("OIDC_JWKS_REJECTED")
        seen_kids.add(kid)
        validated.append(dict(key))
    if not validated:
        raise OidcProtocolError("OIDC_JWKS_REJECTED")
    return {"keys": validated}


def _validate_metadata_registration(
    metadata: KeycloakMetadata, registration: OidcClientRegistration
) -> None:
    if not isinstance(metadata, KeycloakMetadata):
        raise OidcProtocolError("OIDC_METADATA_REJECTED")
    expected = KeycloakMetadata.validate(
        registration,
        {
            "issuer": metadata.issuer,
            "authorization_endpoint": metadata.authorization_endpoint,
            "token_endpoint": metadata.token_endpoint,
            "jwks_uri": metadata.jwks_uri,
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "id_token_signing_alg_values_supported": ["RS256"],
        },
    )
    if metadata != expected:
        raise OidcProtocolError("OIDC_METADATA_REJECTED")


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return ()
    return tuple(value)


def _validate_opaque_value(value: object) -> str:
    if not isinstance(value, str) or _OPAQUE_VALUE.fullmatch(value) is None:
        raise OidcProtocolError("OIDC_AUTHORIZATION_REQUEST_REJECTED")
    return value


def _validate_pkce_verifier(value: object) -> str:
    if not isinstance(value, str) or _PKCE_VERIFIER.fullmatch(value) is None:
        raise OidcProtocolError("OIDC_PKCE_REJECTED")
    return value


def validate_authorization_code(value: object) -> str:
    """Validate an authorization code before any durable callback is consumed."""
    if not isinstance(value, str) or _AUTHORIZATION_CODE.fullmatch(value) is None:
        raise OidcProtocolError("OIDC_CODE_REJECTED")
    return value


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _bounded_token(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= _MAX_TOKEN_BYTES
        or not value.isascii()
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError
    return value


def _validate_expires_in(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
        raise ValueError
    return value


def _valid_access_token_hash(value: object, access_token: str) -> bool:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{22}", value) is None:
        return False
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest[: len(digest) // 2]).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(value, expected)


def _optional_numeric_date(value: object, now: int) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > now + _CLOCK_SKEW_SECONDS
    ):
        raise ValueError
    return value


def _optional_bounded_string(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError
    return value


def _verified_email_claim(claims: Mapping[str, Any]) -> str | None:
    verification = claims.get("email_verified")
    claimed_email = claims.get("email")
    if verification is not None and not isinstance(verification, bool):
        raise ValueError
    if claimed_email is not None and (
        not isinstance(claimed_email, str) or not 3 <= len(claimed_email) <= 320
    ):
        raise ValueError
    if verification is not True:
        return None
    return normalize_ascii_mailbox(claimed_email)


def _trusted_tls_verifier(value: ssl.SSLContext | bool) -> ssl.SSLContext | bool:
    if value is not True and not isinstance(value, ssl.SSLContext):
        raise OidcProtocolError("OIDC_TLS_CONFIGURATION_REJECTED")
    return value
