"""Run an isolated Keycloak authorization-code/PKCE protocol qualification."""

import asyncio
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import ssl
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from uuid import uuid4

import httpx2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lab_runtime import runtime_root

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.oidc_login import (  # noqa: E402
    ConsumedOidcLoginAttempt,
    OidcClientRegistration,
)
from signal_core.oidc_protocol import (  # noqa: E402
    KeycloakMetadata,
    OidcProtocolError,
    create_authorization_request,
    discover_keycloak,
    exchange_authorization_code,
    fetch_jwks,
    validate_id_token,
)

REALM_PATH = ROOT / "experiments/keycloak-feasibility/realm.json"
IMAGE = (
    "quay.io/keycloak/keycloak:26.7.3@"
    "sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54"
)
LABEL = "io.signal.keycloak-lab.run"
CLIENT_ID = "signal-lab-dashboard"
REDIRECT_URI = "https://127.0.0.1/callback"
EXPECTED_VERSION = "Keycloak 26.7.3"
KEYCLOAK_STARTUP_TIMEOUT_SECONDS = 180.0
KEYCLOAK_STARTUP_POLL_SECONDS = 0.25


class LabError(RuntimeError):
    """Failure safe to print without provider responses or protocol secrets."""


@dataclass(frozen=True)
class AuthorizationGrant:
    code: str = field(repr=False)
    code_verifier: str = field(repr=False)
    nonce: str = field(repr=False)


class LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.hidden_inputs: dict[str, str] = {}
        self._in_login_form = False

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        values = dict(attributes)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values.get("action")
            self._in_login_form = True
        if (
            tag == "input"
            and self._in_login_form
            and values.get("type", "").lower() == "hidden"
            and values.get("name")
            and values.get("value") is not None
        ):
            self.hidden_inputs[values["name"]] = values["value"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._in_login_form:
            self._in_login_form = False


def require_free_space(directory: Path, minimum: int = 3 * 1024**3) -> None:
    if shutil.disk_usage(directory).free < minimum:
        raise LabError("At least 3 GiB free is required before starting the Keycloak lab.")


def docker(*args: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            ["docker", *args], check=True, text=True, capture_output=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as error:
        operation = args[0] if args else "command"
        raise LabError(f"Docker {operation} failed ({type(error).__name__}).") from None
    return result.stdout.strip()


def cleanup(name: str) -> None:
    try:
        names = docker(
            "container",
            "ls",
            "--all",
            "--filter",
            f"label={LABEL}={name}",
            "--format",
            "{{.Names}}",
        ).splitlines()
        if name in names:
            docker("container", "rm", "--force", "--volumes", name)
    except LabError:
        raise LabError(f"Cleanup unconfirmed for {name}; inspect this run only.") from None


def keycloak_container_user() -> str:
    """Match the private bind owner without granting root container execution."""
    try:
        uid = os.getuid()
    except AttributeError:
        raise LabError("The Keycloak lab requires a POSIX host user.") from None
    if uid <= 0:
        raise LabError("The Keycloak lab must run as a non-root host user.")
    return f"{uid}:0"


def wait_for_keycloak(
    client: httpx2.Client,
    discovery: str,
    *,
    timeout_seconds: float = KEYCLOAK_STARTUP_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait through bounded transient startup failures for exact OIDC discovery."""
    deadline = monotonic() + timeout_seconds
    while True:
        try:
            response = client.get(discovery, headers={"Accept": "application/json"})
            if response.status_code == 200:
                return
        except httpx2.HTTPError:
            pass
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise LabError(
                f"Disposable Keycloak did not become ready within {timeout_seconds:g} seconds."
            )
        sleep(min(KEYCLOAK_STARTUP_POLL_SECONDS, remaining))


@contextmanager
def isolated_keycloak():
    require_free_space(ROOT)
    docker("info", "--format", "{{.ServerVersion}}", timeout=10)
    docker("image", "pull", IMAGE, timeout=300)
    docker("image", "inspect", IMAGE, "--format", "{{.Id}}", timeout=30)
    container_user = keycloak_container_user()
    name = f"signal-keycloak-tests-{secrets.token_hex(6)}"
    certificate_directory = runtime_root(ROOT) / "keycloak-tests" / name
    certificate_directory.mkdir(parents=True, mode=0o700)
    try:
        certificate_path, key_path = _generate_certificate(certificate_directory)
        print(f"Starting isolated Keycloak project {name}.", flush=True)
        docker(
            "container",
            "create",
            "--name",
            name,
            "--label",
            f"{LABEL}={name}",
            "--user",
            container_user,
            "--publish",
            "127.0.0.1::8443",
            "--memory",
            "768m",
            "--cpus",
            "1",
            "--pids-limit",
            "256",
            "--security-opt",
            "no-new-privileges:true",
            "--mount",
            f"type=bind,src={REALM_PATH},dst=/opt/keycloak/data/import/signal-lab-realm.json,ro",
            "--mount",
            f"type=bind,src={certificate_path},dst=/opt/keycloak/conf/signal-lab.crt,ro",
            "--mount",
            f"type=bind,src={key_path},dst=/opt/keycloak/conf/signal-lab.key,ro",
            IMAGE,
            "start-dev",
            "--import-realm",
            "--http-enabled=false",
            "--https-certificate-file=/opt/keycloak/conf/signal-lab.crt",
            "--https-certificate-key-file=/opt/keycloak/conf/signal-lab.key",
            timeout=180,
        )
        docker("container", "start", name)
        description = json.loads(docker("inspect", name))[0]
        bindings = description["NetworkSettings"]["Ports"].get("8443/tcp")
        if not bindings or len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
            raise LabError("Keycloak must publish exactly one ephemeral loopback port.")
        base_url = f"https://127.0.0.1:{bindings[0]['HostPort']}"
        discovery = f"{base_url}/realms/signal-lab/.well-known/openid-configuration"
        tls_context = ssl.create_default_context(cafile=str(certificate_path))
        with httpx2.Client(
            timeout=2,
            follow_redirects=False,
            trust_env=False,
            verify=tls_context,
        ) as client:
            wait_for_keycloak(client, discovery)
        version_output = docker("container", "exec", name, "/opt/keycloak/bin/kc.sh", "--version")
        version = version_output.splitlines()[0] if version_output else ""
        if version != EXPECTED_VERSION:
            raise LabError("Unexpected Keycloak version for the pinned test profile.")
        yield base_url, version, tls_context
    finally:
        cleanup_error = None
        try:
            cleanup(name)
        except LabError as error:
            cleanup_error = error
        certificate_cleanup_failed = False
        try:
            shutil.rmtree(certificate_directory)
        except OSError:
            certificate_cleanup_failed = True
        if cleanup_error is not None:
            raise cleanup_error
        if certificate_cleanup_failed:
            raise LabError(f"TLS fixture cleanup unconfirmed for {name}.")


def source_hashes() -> dict[str, str]:
    files = [
        ROOT / path
        for path in [
            "requirements.txt",
            "scripts/keycloak_lab.py",
            "scripts/lab_runtime.py",
            "services/control_plane/src/signal_core/identity_conditions.py",
            "services/control_plane/src/signal_core/oidc_login.py",
            "services/control_plane/src/signal_core/oidc_protocol.py",
            "experiments/keycloak-feasibility/realm.json",
            "tests/identity/test_oidc_protocol.py",
            "tests/tooling/test_keycloak_lab.py",
        ]
    ]
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }


async def authorize(
    client: httpx2.AsyncClient,
    registration: OidcClientRegistration,
    metadata: KeycloakMetadata,
    username: str,
    password: str,
) -> AuthorizationGrant:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    authorization_url = await create_authorization_request(
        registration,
        metadata,
        state=state,
        nonce=nonce,
        code_verifier=verifier,
    )
    try:
        response = await client.get(authorization_url)
        for _ in range(10):
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = urljoin(str(response.url), response.headers.get("location", ""))
            if _same_callback(location, registration.redirect_uri):
                return _grant_from_callback(location, state, verifier, nonce)
            _require_provider_url(location, registration.issuer)
            response = await client.get(location)
        if response.status_code != 200:
            raise LabError(
                f"Keycloak authorization page was unavailable (status={response.status_code})."
            )
        parser = LoginFormParser()
        parser.feed(response.text)
        if not parser.action:
            raise LabError("Keycloak login form contract changed.")
        form_url = urljoin(str(response.url), parser.action)
        _require_provider_url(form_url, registration.issuer)
        form = dict(parser.hidden_inputs)
        form.update({"username": username, "password": password, "credentialId": ""})
        response = await client.post(form_url, data=form)
        for _ in range(10):
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = urljoin(str(response.url), response.headers.get("location", ""))
            if _same_callback(location, registration.redirect_uri):
                return _grant_from_callback(location, state, verifier, nonce)
            _require_provider_url(location, registration.issuer)
            response = await client.get(location)
    except httpx2.HTTPError:
        raise LabError("Keycloak browser protocol request failed.") from None
    returned_form = LoginFormParser()
    returned_form.feed(response.text)
    raise LabError(
        "Keycloak did not complete the authorization redirect "
        f"(status={response.status_code}, login_form={returned_form.action is not None})."
    )


async def run_protocol_checks(base_url: str, tls_context: ssl.SSLContext) -> list[dict[str, str]]:
    fixture = json.loads(REALM_PATH.read_text())
    user = fixture["users"][0]
    registration = OidcClientRegistration(
        issuer=f"{base_url}/realms/{fixture['realm']}",
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
    )
    metadata = await discover_keycloak(registration, verify=tls_context)
    jwks = await fetch_jwks(registration, metadata, verify=tls_context)
    checks = [
        {"name": "exact discovery and public RS256 JWKS", "status": "PASS"},
    ]
    async with httpx2.AsyncClient(
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=tls_context,
    ) as browser:
        grant = await authorize(
            browser,
            registration,
            metadata,
            user["username"],
            user["credentials"][0]["value"],
        )
        token = await exchange_authorization_code(
            registration,
            metadata,
            code=grant.code,
            code_verifier=grant.code_verifier,
            verify=tls_context,
        )
        attempt = _consumed_attempt(registration, grant.nonce)
        identity = validate_id_token(token, attempt=attempt, jwks=jwks)
        if (
            identity.client_id != CLIENT_ID
            or identity.issuer != registration.issuer
            or identity.verified_email != user["email"]
            or identity.authentication_context != "1"
            or type(identity.auth_time) is not int
        ):
            raise LabError("Validated identity did not match the fixed registration.")
        checks.append(
            {
                "name": "authorization code PKCE and session-ready signed ID token",
                "status": "PASS",
            }
        )

        try:
            await exchange_authorization_code(
                registration,
                metadata,
                code=grant.code,
                code_verifier=grant.code_verifier,
                verify=tls_context,
            )
        except OidcProtocolError:
            checks.append({"name": "authorization code replay rejected", "status": "PASS"})
        else:
            raise LabError("Keycloak accepted an authorization code replay.")

        wrong_pkce_grant = await authorize(
            browser,
            registration,
            metadata,
            user["username"],
            user["credentials"][0]["value"],
        )
        try:
            await exchange_authorization_code(
                registration,
                metadata,
                code=wrong_pkce_grant.code,
                code_verifier="x" * 43,
                verify=tls_context,
            )
        except OidcProtocolError:
            checks.append({"name": "wrong PKCE verifier rejected", "status": "PASS"})
        else:
            raise LabError("Keycloak accepted a wrong PKCE verifier.")

        try:
            validate_id_token(
                token,
                attempt=_consumed_attempt(registration, "x" * 43),
                jwks=jwks,
            )
        except OidcProtocolError:
            checks.append({"name": "wrong ID-token nonce rejected", "status": "PASS"})
        else:
            raise LabError("Signal accepted an ID token with the wrong nonce binding.")
    return checks


def _consumed_attempt(registration: OidcClientRegistration, nonce: str) -> ConsumedOidcLoginAttempt:
    return ConsumedOidcLoginAttempt(
        id=uuid4(),
        registration=registration,
        nonce_hash=hashlib.sha256(nonce.encode("ascii")).digest(),
        pkce_secret_reference="secret://identity/pkce/disposable-lab",
        return_path="/",
    )


def _generate_certificate(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Signal disposable lab")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate_path = directory / "certificate.pem"
    key_path = directory / "private-key.pem"
    _write_private_file(
        certificate_path,
        certificate.public_bytes(serialization.Encoding.PEM),
    )
    _write_private_file(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    return certificate_path, key_path


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(content)


def _require_provider_url(url: str, issuer: str) -> None:
    candidate = urlsplit(url)
    trusted = urlsplit(issuer)
    if (
        candidate.scheme != trusted.scheme
        or candidate.netloc != trusted.netloc
        or not candidate.path.startswith(f"{trusted.path}/")
        or candidate.fragment
    ):
        raise LabError("Keycloak browser flow attempted an untrusted redirect.")


def _same_callback(candidate: str, redirect_uri: str) -> bool:
    actual = urlsplit(candidate)
    expected = urlsplit(redirect_uri)
    return actual._replace(query="", fragment="") == expected


def _grant_from_callback(
    location: str, state: str, verifier: str, nonce: str
) -> AuthorizationGrant:
    query = parse_qs(urlsplit(location).query, keep_blank_values=True)
    if query.get("state") != [state] or len(query.get("code", [])) != 1:
        raise LabError("Keycloak callback did not preserve the exact state and code.")
    return AuthorizationGrant(code=query["code"][0], code_verifier=verifier, nonce=nonce)


def main() -> int:
    hashes = source_hashes()
    try:
        with isolated_keycloak() as (base_url, version, tls_context):
            checks = asyncio.run(run_protocol_checks(base_url, tls_context))
        if hashes != source_hashes():
            raise LabError("Source changed during verification; rerun against stable source.")
        report = {
            "schema_version": 1,
            "recorded_at": datetime.now(UTC).isoformat(),
            "provider": version,
            "image": IMAGE,
            "python": sys.version.split()[0],
            "source_sha256": hashes,
            "tests": checks,
            "passed": len(checks),
            "production_authority": False,
            "synthetic_identity_only": True,
            "token_material_recorded": False,
            "transport_security": "ephemeral locally trusted TLS",
            "cleanup": "completed",
        }
        directory = runtime_root(ROOT) / "keycloak-tests"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
        return 0
    except LabError as error:
        print(str(error), file=sys.stderr)
    except OidcProtocolError as error:
        print(f"Keycloak protocol qualification failed ({error.code}).", file=sys.stderr)
    except Exception as error:
        print(f"Keycloak lab failed safely ({type(error).__name__}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
