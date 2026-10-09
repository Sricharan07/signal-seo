import base64
import hashlib
import json
from dataclasses import replace
from time import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx2
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from signal_core.oidc_login import ConsumedOidcLoginAttempt, OidcClientRegistration
from signal_core.oidc_protocol import (
    KeycloakMetadata,
    OidcProtocolError,
    OidcTokenResponse,
    create_authorization_request,
    discover_keycloak,
    exchange_authorization_code,
    fetch_jwks,
    validate_id_token,
)

ISSUER = "https://identity.example.test/realms/signal"
CLIENT_ID = "signal-dashboard"
REDIRECT_URI = "https://dashboard.example.test/auth/callback"
STATE = "s" * 43
NONCE = "n" * 43
VERIFIER = "v" * 43
ACCESS_TOKEN = "access-token"
NOW = int(time())
KEY = RSAKey.generate_key(parameters={"kid": "signal-test-key", "use": "sig", "alg": "RS256"})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def registration() -> OidcClientRegistration:
    return OidcClientRegistration(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
    )


@pytest.fixture
def metadata(registration: OidcClientRegistration) -> KeycloakMetadata:
    return valid_metadata(registration)


@pytest.fixture
def attempt(registration: OidcClientRegistration) -> ConsumedOidcLoginAttempt:
    return ConsumedOidcLoginAttempt(
        id=uuid4(),
        registration=registration,
        nonce_hash=hashlib.sha256(NONCE.encode("ascii")).digest(),
        pkce_secret_reference="secret://identity/pkce/test",
        return_path="/dashboard",
    )


def valid_metadata(registration: OidcClientRegistration) -> KeycloakMetadata:
    prefix = f"{registration.issuer}/protocol/openid-connect"
    return KeycloakMetadata(
        issuer=registration.issuer,
        authorization_endpoint=f"{prefix}/auth",
        token_endpoint=f"{prefix}/token",
        jwks_uri=f"{prefix}/certs",
    )


def metadata_document(registration: OidcClientRegistration) -> dict:
    metadata = valid_metadata(registration)
    return {
        "issuer": metadata.issuer,
        "authorization_endpoint": metadata.authorization_endpoint,
        "token_endpoint": metadata.token_endpoint,
        "jwks_uri": metadata.jwks_uri,
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


def public_jwks(key: RSAKey = KEY) -> dict:
    return {"keys": [key.as_dict(private=False)]}


def access_token_hash(access_token: str = ACCESS_TOKEN) -> str:
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode("ascii")


def id_token(claim_overrides: dict | None = None, *, key: RSAKey = KEY, alg: str = "RS256") -> str:
    claims = {
        "iss": ISSUER,
        "sub": "provider-user-1",
        "aud": CLIENT_ID,
        "exp": NOW + 300,
        "iat": NOW,
        "nonce": NONCE,
        "at_hash": access_token_hash(),
        "auth_time": NOW - 5,
        "sid": "provider-session-1",
        "acr": "1",
        "email": "Invitee@Example.TEST",
        "email_verified": True,
    }
    claims.update(claim_overrides or {})
    return jwt.encode(
        {"alg": alg, "kid": key.as_dict(private=False)["kid"]},
        claims,
        key,
        algorithms=[alg],
    )


def token_response(token: str | None = None) -> OidcTokenResponse:
    return OidcTokenResponse(
        id_token=token or id_token(),
        access_token=ACCESS_TOKEN,
        expires_in=300,
    )


def test_signed_authentication_methods_are_preserved(attempt):
    identity = validate_id_token(
        token_response(id_token({"amr": ["otp", "pwd"]})),
        attempt=attempt,
        jwks=public_jwks(),
        now=NOW,
    )
    assert identity.authentication_methods == frozenset({"otp", "pwd"})


@pytest.mark.parametrize("methods", [[], "otp", ["otp", "otp"], [1], ["bad method"], ["a"] * 17])
def test_malformed_signed_authentication_methods_fail_closed(attempt, methods):
    with pytest.raises(OidcProtocolError, match="OIDC_ID_TOKEN_REJECTED"):
        validate_id_token(
            token_response(id_token({"amr": methods})),
            attempt=attempt,
            jwks=public_jwks(),
            now=NOW,
        )


@pytest.mark.parametrize(
    "values",
    [
        {"id_token": ""},
        {"access_token": "t\u00f6ken"},
        {"expires_in": 0},
        {"expires_in": 3601},
    ],
)
def test_token_response_cannot_be_constructed_with_unbounded_values(values: dict):
    parameters = {"id_token": "id", "access_token": "access", "expires_in": 300}
    parameters.update(values)
    with pytest.raises(ValueError):
        OidcTokenResponse(**parameters)


@pytest.mark.anyio
async def test_discovery_accepts_only_exact_keycloak_endpoints(
    registration: OidcClientRegistration,
):
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url == f"{ISSUER}/.well-known/openid-configuration"
        assert request.headers["accept"] == "application/json"
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json=metadata_document(registration),
        )

    metadata = await discover_keycloak(registration, transport=httpx2.MockTransport(handler))
    assert metadata == valid_metadata(registration)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("issuer", "https://attacker.example/realms/signal"),
        ("authorization_endpoint", "https://attacker.example/auth"),
        ("token_endpoint", "https://attacker.example/token"),
        ("jwks_uri", "https://attacker.example/certs"),
        ("response_types_supported", ["token"]),
        ("code_challenge_methods_supported", ["plain"]),
        ("id_token_signing_alg_values_supported", ["HS256"]),
    ],
)
async def test_discovery_rejects_redirected_or_downgraded_metadata(
    registration: OidcClientRegistration, field: str, value: object
):
    document = metadata_document(registration)
    document[field] = value
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(200, headers={"content-type": "application/json"}, json=document)
    )
    with pytest.raises(OidcProtocolError, match="OIDC_METADATA_REJECTED"):
        await discover_keycloak(registration, transport=transport)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(302, headers={"location": "https://attacker.example/metadata"}),
        httpx2.Response(200, headers={"content-type": "text/html"}, text="not json"),
        httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"x" * (128 * 1024 + 1),
        ),
    ],
)
async def test_provider_documents_fail_closed_without_following_redirects(
    registration: OidcClientRegistration, response: httpx2.Response
):
    with pytest.raises(OidcProtocolError, match="OIDC_PROVIDER_DOCUMENT_REJECTED"):
        await discover_keycloak(
            registration,
            transport=httpx2.MockTransport(lambda _: response),
        )


@pytest.mark.anyio
async def test_tls_verification_cannot_be_disabled(registration: OidcClientRegistration):
    with pytest.raises(OidcProtocolError, match="OIDC_TLS_CONFIGURATION_REJECTED"):
        await discover_keycloak(
            registration,
            transport=httpx2.MockTransport(lambda _: pytest.fail("No request is allowed")),
            verify=False,
        )


@pytest.mark.anyio
async def test_authorization_request_contains_exact_state_nonce_and_s256_challenge(
    registration: OidcClientRegistration, metadata: KeycloakMetadata
):
    url = await create_authorization_request(
        registration,
        metadata,
        state=STATE,
        nonce=NONCE,
        code_verifier=VERIFIER,
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert parsed._replace(query="").geturl() == metadata.authorization_endpoint
    assert query == {
        "response_type": ["code"],
        "client_id": [CLIENT_ID],
        "redirect_uri": [REDIRECT_URI],
        "scope": ["openid email"],
        "state": [STATE],
        "nonce": [NONCE],
        "code_challenge": [expected_challenge],
        "code_challenge_method": ["S256"],
        "max_age": ["0"],
    }
    assert VERIFIER not in url


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["state", "nonce", "code_verifier"])
async def test_authorization_request_rejects_malformed_browser_secrets(
    registration: OidcClientRegistration, metadata: KeycloakMetadata, field: str
):
    values = {"state": STATE, "nonce": NONCE, "code_verifier": VERIFIER}
    values[field] = "short"
    with pytest.raises(OidcProtocolError):
        await create_authorization_request(registration, metadata, **values)


@pytest.mark.anyio
async def test_code_exchange_uses_public_client_exact_redirect_and_pkce(
    registration: OidcClientRegistration, metadata: KeycloakMetadata
):
    observed = {}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        observed.update(parse_qs((await request.aread()).decode("ascii")))
        assert request.url == metadata.token_endpoint
        assert request.method == "POST"
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "id_token": "id-token",
                "access_token": "access-token",
                "expires_in": 300,
                "token_type": "Bearer",
                "refresh_token": "ignored-not-returned",
            },
        )

    result = await exchange_authorization_code(
        registration,
        metadata,
        code="authorization-code",
        code_verifier=VERIFIER,
        transport=httpx2.MockTransport(handler),
    )
    assert result == OidcTokenResponse(
        id_token="id-token", access_token="access-token", expires_in=300
    )
    assert observed == {
        "grant_type": ["authorization_code"],
        "redirect_uri": [REDIRECT_URI],
        "code": ["authorization-code"],
        "code_verifier": [VERIFIER],
        "client_id": [CLIENT_ID],
    }
    assert "refresh_token" not in result.__dict__
    assert "access-token" not in repr(result)
    assert "id-token" not in repr(result)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider_response",
    [
        {"error": "invalid_grant", "error_description": "secret-code"},
        {"id_token": "id", "access_token": "access", "expires_in": 0, "token_type": "Bearer"},
        {"id_token": "id", "access_token": "access", "expires_in": 1, "token_type": "MAC"},
        {"id_token": "id", "access_token": "access", "expires_in": 3601, "token_type": "Bearer"},
        {"id_token": "id", "access_token": "t\u00f6ken", "expires_in": 1, "token_type": "Bearer"},
        {
            "id_token": "id",
            "access_token": "access",
            "expires_in": 1,
            "token_type": "Bearer",
            "padding": "x" * (128 * 1024),
        },
    ],
)
async def test_code_exchange_returns_only_safe_fixed_errors(
    registration: OidcClientRegistration,
    metadata: KeycloakMetadata,
    provider_response: dict,
):
    status = 400 if "error" in provider_response else 200
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(
            status, headers={"content-type": "application/json"}, json=provider_response
        )
    )
    with pytest.raises(OidcProtocolError) as raised:
        await exchange_authorization_code(
            registration,
            metadata,
            code="secret-code",
            code_verifier=VERIFIER,
            transport=transport,
        )
    assert "secret-code" not in str(raised.value)
    assert raised.value.code in {"OIDC_CODE_EXCHANGE_FAILED", "OIDC_TOKEN_RESPONSE_REJECTED"}


def test_signed_id_token_is_validated_before_returning_bounded_identity(
    attempt: ConsumedOidcLoginAttempt,
):
    identity = validate_id_token(token_response(), attempt=attempt, jwks=public_jwks(), now=NOW)
    assert identity.issuer == ISSUER
    assert identity.subject == "provider-user-1"
    assert identity.client_id == CLIENT_ID
    assert identity.issued_at == NOW
    assert identity.expires_at == NOW + 300
    assert identity.auth_time == NOW - 5
    assert identity.provider_session_id == "provider-session-1"
    assert identity.authentication_context == "1"
    assert identity.verified_email == "invitee@example.test"
    assert "invitee@example.test" not in repr(identity)


def test_unverified_provider_email_never_becomes_an_identity_condition(
    attempt: ConsumedOidcLoginAttempt,
):
    identity = validate_id_token(
        token_response(id_token({"email_verified": False})),
        attempt=attempt,
        jwks=public_jwks(),
        now=NOW,
    )
    assert identity.verified_email is None


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.example/realms/signal"},
        {"aud": "different-client"},
        {"nonce": "x" * 43},
        {"exp": NOW - 31},
        {"iat": NOW + 31},
        {"iat": NOW - 700, "exp": NOW + 1},
        {"exp": NOW + 601},
        {"aud": [CLIENT_ID, "another"], "azp": None},
        {"aud": [CLIENT_ID, "another"], "azp": "another"},
        {"at_hash": "wrong"},
        {"sub": ""},
        {"sub": "subject\u0000suffix"},
        {"auth_time": NOW + 31},
        {"email_verified": "true"},
        {"email_verified": True, "email": None},
        {"email_verified": True, "email": ".invalid@example.test"},
    ],
)
def test_id_token_rejects_wrong_binding_and_time_claims(
    attempt: ConsumedOidcLoginAttempt, claims: dict
):
    with pytest.raises(OidcProtocolError, match="OIDC_ID_TOKEN_REJECTED"):
        validate_id_token(
            token_response(id_token(claims)),
            attempt=attempt,
            jwks=public_jwks(),
            now=NOW,
        )


def test_id_token_rejects_wrong_signature(attempt: ConsumedOidcLoginAttempt):
    attacker_key = RSAKey.generate_key(
        parameters={"kid": "signal-test-key", "use": "sig", "alg": "RS256"}
    )
    with pytest.raises(OidcProtocolError, match="OIDC_ID_TOKEN_REJECTED"):
        validate_id_token(
            token_response(id_token(key=attacker_key)),
            attempt=attempt,
            jwks=public_jwks(),
            now=NOW,
        )


@pytest.mark.parametrize(
    "jwks",
    [
        {},
        {"keys": []},
        {"keys": [{**KEY.as_dict(private=False), "alg": "HS256"}]},
        {"keys": [{**KEY.as_dict(private=False), "d": "private-material"}]},
        {"keys": [KEY.as_dict(private=False), KEY.as_dict(private=False)]},
    ],
)
def test_jwks_rejects_empty_symmetric_private_or_duplicate_keys(
    attempt: ConsumedOidcLoginAttempt, jwks: dict
):
    with pytest.raises(OidcProtocolError, match="OIDC_JWKS_REJECTED"):
        validate_id_token(token_response(), attempt=attempt, jwks=jwks, now=NOW)


@pytest.mark.anyio
async def test_jwks_fetch_accepts_public_rs256_keys(
    registration: OidcClientRegistration, metadata: KeycloakMetadata
):
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(
            200,
            headers={"content-type": "application/jwk-set+json"},
            content=json.dumps(public_jwks()).encode(),
            request=request,
        )
    )
    assert await fetch_jwks(registration, metadata, transport=transport) == public_jwks()


@pytest.mark.anyio
async def test_jwks_fetch_revalidates_destination_before_network(
    registration: OidcClientRegistration, metadata: KeycloakMetadata
):
    forged = replace(metadata, jwks_uri="https://attacker.example/certs")
    transport = httpx2.MockTransport(lambda _: pytest.fail("No request is allowed"))
    with pytest.raises(OidcProtocolError, match="OIDC_METADATA_REJECTED"):
        await fetch_jwks(registration, forged, transport=transport)


def test_metadata_object_cannot_be_reused_with_another_registration(
    registration: OidcClientRegistration, metadata: KeycloakMetadata
):
    other = replace(registration, client_id="another-client")
    # A different client can share provider metadata; a different issuer cannot.
    assert KeycloakMetadata.validate(other, metadata_document(other)) == metadata
    moved = replace(registration, issuer="https://identity.example.test/realms/other")
    with pytest.raises(OidcProtocolError, match="OIDC_METADATA_REJECTED"):
        KeycloakMetadata.validate(moved, metadata_document(registration))
