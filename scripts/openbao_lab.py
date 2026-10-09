"""Qualify PKCE secret storage against an isolated, TLS-enabled OpenBao server."""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import shutil
import ssl
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx2
from joserfc.jwk import RSAKey
from lab_runtime import runtime_root

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.artifact_keys import OpenBaoBrandArtifactKey  # noqa: E402
from signal_core.assistant_credentials import OpenBaoAssistantCredentials  # noqa: E402
from signal_core.bing_secrets import BingSecretError, OpenBaoBingSecrets  # noqa: E402
from signal_core.dataforseo_credentials import (  # noqa: E402
    DataForSeoUnavailable,
    OpenBaoDataForSeoCredentials,
)
from signal_core.docs_secrets import OpenBaoDocsSecrets  # noqa: E402
from signal_core.ga4_secrets import OpenBaoGa4Secrets  # noqa: E402
from signal_core.github_read_binding import OpenBaoGitHubAppCredential  # noqa: E402
from signal_core.gsc_secrets import GscSecretError, OpenBaoGscSecrets  # noqa: E402
from signal_core.indexnow_secrets import (  # noqa: E402
    IndexNowSecretUnavailable,
    OpenBaoIndexNowKeys,
)
from signal_core.model_credentials import (  # noqa: E402
    OpenBaoJevCredential,
    OpenBaoModelCredential,
)
from signal_core.openbao_http import request as openbao_request  # noqa: E402
from signal_core.pagespeed_credentials import (  # noqa: E402
    OpenBaoPageSpeedCredentials,
    PageSpeedCredentialUnavailable,
)
from signal_core.pkce_secrets import (  # noqa: E402
    OpenBaoPkceClient,
    PkceSecretError,
    PkceSecretUnavailable,
)
from signal_core.recovery_authority import (  # noqa: E402
    OpenBaoRecoveryAuthority,
    RecoveryAuthorityError,
)
from signal_core.slack_protocol import SlackRejected  # noqa: E402
from signal_core.slack_secrets import OpenBaoSlackSecrets  # noqa: E402
from signal_core.smtp_submission import OpenBaoSmtpCredential  # noqa: E402
from signal_core.telegram_protocol import TelegramRejected  # noqa: E402
from signal_core.telegram_secrets import OpenBaoTelegramSecrets  # noqa: E402
from signal_core.wordpress_protocol import WordPressUnavailable  # noqa: E402
from signal_core.wordpress_secrets import OpenBaoWordPressSecrets  # noqa: E402

IMAGE = (
    "ghcr.io/openbao/openbao:2.6.1@"
    "sha256:5b2486ab0fb90bbc788cc345b0a08616dfb375873ee8be5df3a2fd4d378a67e0"
)
EXPECTED_VERSION = "OpenBao v2.6.1"
LABEL = "io.signal.openbao-lab.run"
MOUNT = "signal-ephemeral"
AUTHORITY_MOUNT = "signal-authority"
WRITER_POLICY = "signal-pkce-writer"
CONSUMER_POLICY = "signal-pkce-consumer"
RECOVERY_POLICY = "signal-recovery-reader"
MODEL_MOUNT = "signal-model"
MODEL_READER_POLICY = "signal-model-reader"
DECISION_MOUNT = "signal-decision"
DECISION_READER_POLICY = "signal-decision-reader"
GITHUB_MOUNT = "signal-github"
GITHUB_READER_POLICY = "signal-github-reader"
GSC_MOUNT = "signal-gsc"
GSC_POLICY = "signal-gsc-connector"
BING_MOUNT = "signal-bing"
BING_POLICY = "signal-bing-connector"
ASSISTANT_MOUNT = "signal-assistants"
ASSISTANT_POLICY = "signal-assistant-reader"
ARTIFACT_MOUNT = "signal-artifacts"
ARTIFACT_READER_POLICY = "signal-brand-artifact-reader"


class LabError(RuntimeError):
    """Failure safe to print without OpenBao tokens, responses, or stored secrets."""


@dataclass(frozen=True)
class LabServer:
    base_url: str
    version: str
    tls_context: ssl.SSLContext = field(repr=False)
    root_token: str = field(repr=False)


def require_free_space(directory: Path, minimum: int = 3 * 1024**3) -> None:
    if shutil.disk_usage(directory).free < minimum:
        raise LabError("At least 3 GiB free is required before starting the OpenBao lab.")


def docker(*args: str, timeout: int = 30, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            ["docker", *args],
            check=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
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


@contextmanager
def isolated_openbao():
    require_free_space(ROOT)
    docker("info", "--format", "{{.ServerVersion}}", timeout=10)
    docker("image", "pull", IMAGE, timeout=300)
    docker("image", "inspect", IMAGE, "--format", "{{.Id}}", timeout=30)
    name = f"signal-openbao-tests-{secrets.token_hex(6)}"
    root_token = "root-" + secrets.token_urlsafe(32)
    certificate_directory = runtime_root(ROOT) / "openbao-tests" / name
    certificate_directory.mkdir(parents=True, mode=0o700)
    certificate_path = certificate_directory / "vault-ca.pem"
    print(f"Starting isolated OpenBao project {name}.", flush=True)
    try:
        docker(
            "container",
            "create",
            "--name",
            name,
            "--label",
            f"{LABEL}={name}",
            "--publish",
            "127.0.0.1::8200",
            "--memory",
            "384m",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp/bao-tls:rw,noexec,nosuid,nodev,size=2m",
            "--env",
            "BAO_DEV_ROOT_TOKEN_ID",
            IMAGE,
            "server",
            "-dev-tls",
            "-dev-tls-cert-dir=/tmp/bao-tls",
            "-dev-listen-address=0.0.0.0:8200",
            "-dev-no-store-token",
            env=dict(os.environ, BAO_DEV_ROOT_TOKEN_ID=root_token),
            timeout=180,
        )
        docker("container", "start", name)
        description = json.loads(docker("inspect", name))[0]
        bindings = description["NetworkSettings"]["Ports"].get("8200/tcp")
        if not bindings or len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
            raise LabError("OpenBao must publish exactly one ephemeral loopback TLS port.")
        certificate_deadline = time.monotonic() + 30
        while True:
            try:
                docker(
                    "container",
                    "exec",
                    name,
                    "test",
                    "-s",
                    "/tmp/bao-tls/vault-ca.pem",
                )
                break
            except LabError:
                if time.monotonic() >= certificate_deadline:
                    raise LabError("Disposable OpenBao did not generate its TLS CA.") from None
                time.sleep(0.1)
        certificate = docker(
            "container",
            "exec",
            name,
            "cat",
            "/tmp/bao-tls/vault-ca.pem",
        )
        _write_private_file(certificate_path, (certificate + "\n").encode("ascii"))
        tls_context = ssl.create_default_context(cafile=str(certificate_path))
        base_url = f"https://localhost:{bindings[0]['HostPort']}"
        deadline = time.monotonic() + 60
        with httpx2.Client(
            base_url=base_url,
            timeout=2,
            follow_redirects=False,
            trust_env=False,
            verify=tls_context,
            headers={"X-Vault-Token": root_token, "Accept": "application/json"},
        ) as client:
            while True:
                try:
                    response = client.get("/v1/sys/health")
                    if response.status_code == 200:
                        break
                except httpx2.HTTPError:
                    pass
                if time.monotonic() >= deadline:
                    raise LabError("Disposable OpenBao did not become ready.")
                time.sleep(0.2)
        version_output = docker("container", "exec", name, "bao", "version")
        version = version_output.splitlines()[0] if version_output else ""
        if not version.startswith(EXPECTED_VERSION):
            raise LabError("Unexpected OpenBao version for the pinned test profile.")
        yield LabServer(base_url, version, tls_context, root_token)
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


def mount_kv_v2(root, mount: str):
    response = root.post(
        f"/v1/sys/mounts/{mount}", json={"type": "kv", "options": {"version": "2"}}
    )
    if response.status_code != 204:
        return response
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            ready = root.get(f"/v1/{mount}/config", timeout=0.5)
            if ready.status_code == 200 and isinstance(
                _document(ready, "OpenBao KV readiness response invalid.").get("data"), dict
            ):
                return response
            if ready.status_code not in {404, 503}:
                raise LabError("OpenBao KV mount readiness failed.")
        except httpx2.TransportError:
            pass
        time.sleep(0.1)
    raise LabError("OpenBao KV mount readiness timed out.")


def provision(
    server: LabServer,
) -> tuple[OpenBaoPkceClient, OpenBaoPkceClient, OpenBaoRecoveryAuthority, str]:
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, MOUNT),
            204,
            "OpenBao KV mount provisioning failed.",
        )
        _expect(
            root.post(
                f"/v1/{MOUNT}/config",
                json={
                    "max_versions": 1,
                    "cas_required": True,
                    "delete_version_after": "10m",
                },
            ),
            204,
            "OpenBao KV configuration failed.",
        )
        writer_policy = f'path "{MOUNT}/data/oidc-login/*" {{\n  capabilities = ["create"]\n}}\n'
        consumer_policy = (
            f'path "{MOUNT}/data/oidc-login/*" {{\n'
            '  capabilities = ["read"]\n'
            "}\n"
            f'path "{MOUNT}/metadata/oidc-login/*" {{\n'
            '  capabilities = ["delete"]\n'
            "}\n"
        )
        for name, policy in [
            (WRITER_POLICY, writer_policy),
            (CONSUMER_POLICY, consumer_policy),
        ]:
            _expect(
                root.put(f"/v1/sys/policies/acl/{name}", json={"policy": policy}),
                204,
                "OpenBao policy provisioning failed.",
            )
        writer_token = _create_scoped_token(root, WRITER_POLICY)
        consumer_token = _create_scoped_token(root, CONSUMER_POLICY)
        configuration = _document(
            _expect(
                root.get(f"/v1/{MOUNT}/config"),
                200,
                "OpenBao KV configuration verification failed.",
            ),
            "OpenBao KV configuration verification failed.",
        ).get("data")
        if not isinstance(configuration, dict) or (
            configuration.get("max_versions") != 1
            or configuration.get("cas_required") is not True
            or configuration.get("delete_version_after") not in {"10m", "10m0s"}
        ):
            raise LabError("OpenBao KV configuration did not match the bounded profile.")
        generation = "lab-generation-" + secrets.token_hex(16)
        _expect(
            mount_kv_v2(root, AUTHORITY_MOUNT),
            204,
            "OpenBao recovery-authority mount provisioning failed.",
        )
        _expect(
            root.post(
                f"/v1/{AUTHORITY_MOUNT}/config",
                json={"max_versions": 10, "cas_required": True},
            ),
            204,
            "OpenBao recovery-authority configuration failed.",
        )
        _expect(
            root.post(
                f"/v1/{AUTHORITY_MOUNT}/data/recovery/current",
                json={"options": {"cas": 0}, "data": {"generation": generation}},
            ),
            200,
            "OpenBao recovery generation initialization failed.",
        )
        recovery_policy = (
            f'path "{AUTHORITY_MOUNT}/data/recovery/current" {{\n  capabilities = ["read"]\n}}\n'
        )
        _expect(
            root.put(
                f"/v1/sys/policies/acl/{RECOVERY_POLICY}",
                json={"policy": recovery_policy},
            ),
            204,
            "OpenBao recovery-authority policy provisioning failed.",
        )
        recovery_token = _create_scoped_token(root, RECOVERY_POLICY)
        authority_configuration = _document(
            _expect(
                root.get(f"/v1/{AUTHORITY_MOUNT}/config"),
                200,
                "OpenBao recovery-authority verification failed.",
            ),
            "OpenBao recovery-authority verification failed.",
        ).get("data")
        if not isinstance(authority_configuration, dict) or (
            authority_configuration.get("max_versions") != 10
            or authority_configuration.get("cas_required") is not True
            or authority_configuration.get("delete_version_after") not in {"0s", ""}
        ):
            raise LabError("OpenBao recovery authority did not match the bounded profile.")
    return (
        OpenBaoPkceClient(server.base_url, writer_token, MOUNT),
        OpenBaoPkceClient(server.base_url, consumer_token, MOUNT),
        OpenBaoRecoveryAuthority(server.base_url, recovery_token, AUTHORITY_MOUNT),
        generation,
    )


def provision_model_credential(
    server: LabServer,
    api_key: str,
) -> OpenBaoModelCredential:
    """Store one local-only OpenAI key and return a read-only capability."""
    if (
        not isinstance(api_key, str)
        or not api_key.startswith("sk-")
        or not 19 <= len(api_key) <= 499
        or not api_key.isascii()
        or any(character.isspace() for character in api_key)
    ):
        raise LabError("The local model credential is invalid.")
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, MODEL_MOUNT),
            204,
            "OpenBao model-credential mount provisioning failed.",
        )
        _expect(
            root.post(
                f"/v1/{MODEL_MOUNT}/config",
                json={"max_versions": 1, "cas_required": True},
            ),
            204,
            "OpenBao model-credential configuration failed.",
        )
        _expect(
            root.post(
                f"/v1/{MODEL_MOUNT}/data/openai/default",
                json={"options": {"cas": 0}, "data": {"api_key": api_key}},
            ),
            200,
            "OpenBao model-credential initialization failed.",
        )
        policy = f'path "{MODEL_MOUNT}/data/openai/default" {{\n  capabilities = ["read"]\n}}\n'
        _expect(
            root.put(
                f"/v1/sys/policies/acl/{MODEL_READER_POLICY}",
                json={"policy": policy},
            ),
            204,
            "OpenBao model-credential policy provisioning failed.",
        )
        reader_token = _create_scoped_token(root, MODEL_READER_POLICY)
    return OpenBaoModelCredential(server.base_url, reader_token, MODEL_MOUNT)


def provision_brand_artifact_key(
    server: LabServer, key_material: bytes = bytes(range(32))
) -> OpenBaoBrandArtifactKey:
    if not isinstance(key_material, bytes) or len(key_material) != 32:
        raise LabError("A 256-bit artifact key is required.")
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, ARTIFACT_MOUNT),
            204,
            "OpenBao artifact mount provisioning failed.",
        )
        _expect(
            root.post(
                f"/v1/{ARTIFACT_MOUNT}/config",
                json={"max_versions": 1, "cas_required": True},
            ),
            204,
            "OpenBao artifact CAS configuration failed.",
        )
        _expect(
            root.post(
                f"/v1/{ARTIFACT_MOUNT}/data/brand/default",
                json={
                    "options": {"cas": 0},
                    "data": {"key_base64": base64.b64encode(key_material).decode("ascii")},
                },
            ),
            200,
            "OpenBao artifact key initialization failed.",
        )
        policy = f'path "{ARTIFACT_MOUNT}/data/brand/default" {{\n  capabilities = ["read"]\n}}\n'
        _expect(
            root.put(
                f"/v1/sys/policies/acl/{ARTIFACT_READER_POLICY}",
                json={"policy": policy},
            ),
            204,
            "OpenBao artifact policy provisioning failed.",
        )
        token = _create_scoped_token(root, ARTIFACT_READER_POLICY)
    return OpenBaoBrandArtifactKey(server.base_url, token, ARTIFACT_MOUNT)


def provision_assistant_credentials(server: LabServer) -> OpenBaoAssistantCredentials:
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, ASSISTANT_MOUNT),
            204,
            "Assistant mount provisioning failed.",
        )
        _expect(
            root.post(
                f"/v1/{ASSISTANT_MOUNT}/config",
                json={"max_versions": 1, "cas_required": True},
            ),
            204,
            "Assistant mount configuration failed.",
        )
        for provider in ("openai", "perplexity"):
            _expect(
                root.post(
                    f"/v1/{ASSISTANT_MOUNT}/data/{provider}/default",
                    json={
                        "options": {"cas": 0},
                        "data": {"api_key": f"synthetic-{provider}-key-00000000"},
                    },
                ),
                200,
                "Assistant credential initialization failed.",
            )
        policy = "".join(
            f'path "{ASSISTANT_MOUNT}/data/{provider}/default" {{\n  capabilities = ["read"]\n}}\n'
            for provider in ("openai", "perplexity", "gemini")
        )
        _expect(
            root.put(f"/v1/sys/policies/acl/{ASSISTANT_POLICY}", json={"policy": policy}),
            204,
            "Assistant reader policy failed.",
        )
        reader_token = _create_scoped_token(root, ASSISTANT_POLICY)
    return OpenBaoAssistantCredentials(server.base_url, reader_token, ASSISTANT_MOUNT)


def provision_pagespeed_credentials(server: LabServer) -> OpenBaoPageSpeedCredentials:
    mount = "signal-pagespeed"
    policy_name = "signal-pagespeed-reader"
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, mount),
            204,
            "PageSpeed mount provisioning failed.",
        )
        _expect(
            root.post(
                f"/v1/{mount}/data/pagespeed/site",
                json={
                    "options": {"cas": 0},
                    "data": {"api_key": "synthetic-pagespeed-key-00000000"},
                },
            ),
            200,
            "PageSpeed key initialization failed.",
        )
        policy = f'path "{mount}/data/pagespeed/site" {{ capabilities = ["read"] }}'
        _expect(
            root.put(f"/v1/sys/policies/acl/{policy_name}", json={"policy": policy}),
            204,
            "PageSpeed reader policy failed.",
        )
        token = _create_scoped_token(root, policy_name)
    return OpenBaoPageSpeedCredentials(server.base_url, token, f"{mount}/data/pagespeed/site")


def provision_jev_credential(
    server: LabServer,
    api_key: str,
) -> OpenBaoJevCredential:
    """Store one synthetic TypeSafe key and return a read-only capability."""
    if (
        not isinstance(api_key, str)
        or not 16 <= len(api_key) <= 512
        or not api_key.isascii()
        or any(character.isspace() for character in api_key)
    ):
        raise LabError("The local Jev credential is invalid.")
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, DECISION_MOUNT),
            204,
            "OpenBao Jev-credential mount provisioning failed.",
        )
        _expect(
            root.post(
                f"/v1/{DECISION_MOUNT}/config",
                json={"max_versions": 1, "cas_required": True},
            ),
            204,
            "OpenBao Jev-credential configuration failed.",
        )
        _expect(
            root.post(
                f"/v1/{DECISION_MOUNT}/data/typesafe/default",
                json={"options": {"cas": 0}, "data": {"api_key": api_key}},
            ),
            200,
            "OpenBao Jev-credential initialization failed.",
        )
        policy = (
            f'path "{DECISION_MOUNT}/data/typesafe/default" {{\n  capabilities = ["read"]\n}}\n'
        )
        _expect(
            root.put(
                f"/v1/sys/policies/acl/{DECISION_READER_POLICY}",
                json={"policy": policy},
            ),
            204,
            "OpenBao Jev-credential policy provisioning failed.",
        )
        reader_token = _create_scoped_token(root, DECISION_READER_POLICY)
    return OpenBaoJevCredential(server.base_url, reader_token, DECISION_MOUNT)


def provision_github_credential(
    server: LabServer, private_key_pem: str
) -> OpenBaoGitHubAppCredential:
    with httpx2.Client(
        base_url=server.base_url,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, GITHUB_MOUNT),
            204,
            "OpenBao GitHub-credential mount provisioning failed.",
        )
        _expect(
            root.post(
                f"/v1/{GITHUB_MOUNT}/config",
                json={"max_versions": 1, "cas_required": True},
            ),
            204,
            "OpenBao GitHub-credential configuration failed.",
        )
        _expect(
            root.post(
                f"/v1/{GITHUB_MOUNT}/data/github/app",
                json={
                    "options": {"cas": 0},
                    "data": {
                        "app_id": 12345,
                        "private_key_pem": private_key_pem,
                    },
                },
            ),
            200,
            "OpenBao GitHub-credential initialization failed.",
        )
        policy = f'path "{GITHUB_MOUNT}/data/github/app" {{\n  capabilities = ["read"]\n}}\n'
        _expect(
            root.put(
                f"/v1/sys/policies/acl/{GITHUB_READER_POLICY}",
                json={"policy": policy},
            ),
            204,
            "OpenBao GitHub-credential policy provisioning failed.",
        )
        reader_token = _create_scoped_token(root, GITHUB_READER_POLICY)
    return OpenBaoGitHubAppCredential(server.base_url, reader_token, GITHUB_MOUNT)


def provision_gsc_secrets(
    server: LabServer,
    mount: str = GSC_MOUNT,
    policy_name: str = GSC_POLICY,
    store_type: type[OpenBaoGscSecrets] = OpenBaoGscSecrets,
) -> OpenBaoGscSecrets:
    """Provision synthetic GSC secrets with only the connector's KV paths."""
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, mount),
            204,
            "OpenBao GSC mount provisioning failed.",
        )
        _expect(
            root.post(
                f"/v1/{mount}/config",
                json={"max_versions": 1, "cas_required": True},
            ),
            204,
            "OpenBao GSC CAS configuration failed.",
        )
        _expect(
            root.post(
                f"/v1/{mount}/data/oauth-client",
                json={
                    "options": {"cas": 0},
                    "data": {
                        "client_id": "synthetic-client-123.apps.googleusercontent.com",
                        "client_secret": "synthetic-client-secret-123456",
                    },
                },
            ),
            200,
            "OpenBao GSC client initialization failed.",
        )
        policy = (
            f'path "{mount}/data/oauth-client" {{\n  capabilities = ["read"]\n}}\n'
            f'path "{mount}/data/verifiers/*" {{\n'
            '  capabilities = ["create", "read"]\n}\n'
            f'path "{mount}/metadata/verifiers/*" {{\n'
            '  capabilities = ["delete"]\n}\n'
            f'path "{mount}/data/refresh/*" {{\n'
            '  capabilities = ["create", "read", "update"]\n}\n'
            f'path "{mount}/metadata/refresh/*" {{\n'
            '  capabilities = ["delete"]\n}\n'
        )
        _expect(
            root.put(
                f"/v1/sys/policies/acl/{policy_name}",
                json={"policy": policy},
            ),
            204,
            "OpenBao GSC policy provisioning failed.",
        )
        token = _create_scoped_token(root, policy_name)
    return store_type(server.base_url, token, mount)


def provision_bing_secrets(server: LabServer) -> OpenBaoBingSecrets:
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, BING_MOUNT),
            204,
            "OpenBao Bing mount provisioning failed.",
        )
        _expect(
            root.post(f"/v1/{BING_MOUNT}/config", json={"max_versions": 1, "cas_required": True}),
            204,
            "OpenBao Bing CAS configuration failed.",
        )
        _expect(
            root.post(
                f"/v1/{BING_MOUNT}/data/oauth-client",
                json={
                    "options": {"cas": 0},
                    "data": {
                        "client_id": "synthetic-bing-client-123456",
                        "client_secret": "synthetic-bing-secret-123456",
                    },
                },
            ),
            200,
            "OpenBao Bing client initialization failed.",
        )
        policy = (
            f'path "{BING_MOUNT}/data/oauth-client" {{\n  capabilities = ["read"]\n}}\n'
            f'path "{BING_MOUNT}/data/refresh/*" {{\n'
            '  capabilities = ["create", "read", "update"]\n}\n'
            f'path "{BING_MOUNT}/metadata/refresh/*" {{\n'
            '  capabilities = ["delete"]\n}\n'
        )
        _expect(
            root.put(f"/v1/sys/policies/acl/{BING_POLICY}", json={"policy": policy}),
            204,
            "OpenBao Bing policy provisioning failed.",
        )
        token = _create_scoped_token(root, BING_POLICY)
    return OpenBaoBingSecrets(server.base_url, token, BING_MOUNT)


async def check_indexnow_keys(server: LabServer) -> None:
    tenant, site, key_id = uuid4(), uuid4(), uuid4()
    mount = "signal-indexnow"
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, mount),
            204,
            "IndexNow mount failed.",
        )
        _expect(
            root.post(f"/v1/{mount}/config", json={"max_versions": 1, "cas_required": True}),
            204,
            "IndexNow CAS configuration failed.",
        )
        policy = f'path "{mount}/data/{tenant}/{site}/+" {{ capabilities = ["create", "read"] }}'
        _expect(
            root.put("/v1/sys/policies/acl/signal-indexnow-site", json={"policy": policy}),
            204,
            "IndexNow policy failed.",
        )
        token = _create_scoped_token(root, "signal-indexnow-site")
    client = OpenBaoIndexNowKeys(server.base_url, token, mount)
    args = dict(tenant_id=tenant, site_id=site, key_id=key_id, verify=server.tls_context)
    key = await client.key(**args, create=True)
    if await client.key(**args, create=True) != key:
        raise LabError("IndexNow replay changed the key.")
    overwrite = await openbao_request(
        base_url=server.base_url,
        token=token,
        method="POST",
        path=f"/{mount}/data/{tenant}/{site}/{key_id}",
        payload={"options": {"cas": 1}, "data": {"key": "synthetic-replacement-key"}},
        verify=server.tls_context,
    )
    if overwrite.status_code != 403:
        raise LabError("IndexNow key overwrite was not denied.")
    for wrong in ({"tenant_id": uuid4()}, {"site_id": uuid4()}):
        try:
            await client.key(**{**args, **wrong}, create=True)
        except IndexNowSecretUnavailable:
            continue
        raise LabError("IndexNow cross-scope secret access was not denied.")


async def run_checks(
    server: LabServer,
    writer: OpenBaoPkceClient,
    consumer: OpenBaoPkceClient,
    recovery_authority: OpenBaoRecoveryAuthority,
    expected_generation: str,
) -> list[dict[str, str]]:
    checks = [{"name": "TLS KV v2 bounded mount configuration", "status": "PASS"}]
    await check_indexnow_keys(server)
    checks.append({"name": "IndexNow per-site CAS-zero key and ACL isolation", "status": "PASS"})
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token, "Accept": "application/json"},
    ) as root:
        _expect(
            mount_kv_v2(root, "signal-email"),
            204,
            "Email credential mount provisioning failed.",
        )
        _expect(
            root.post(
                "/v1/signal-email/data/smtp/default",
                json={
                    "data": {
                        "username": "synthetic-smtp-user",
                        "password": "synthetic-smtp-password",
                    }
                },
            ),
            200,
            "Email credential provisioning failed.",
        )
        _expect(
            root.put(
                "/v1/sys/policies/acl/signal-email-reader",
                json={
                    "policy": 'path "signal-email/data/smtp/default" { capabilities = ["read"] }'
                },
            ),
            204,
            "Email credential policy provisioning failed.",
        )
        email_token = _create_scoped_token(root, "signal-email-reader")
        _expect(
            mount_kv_v2(root, "signal-slack"),
            204,
            "OpenBao Slack mount failed.",
        )
        _expect(
            root.post("/v1/signal-slack/config", json={"max_versions": 1, "cas_required": True}),
            204,
            "OpenBao Slack configuration failed.",
        )
        _expect(
            root.post(
                "/v1/signal-slack/data/client",
                json={
                    "options": {"cas": 0},
                    "data": {
                        "client_id": "1234567890.1234567890",
                        "client_secret": "synthetic-slack-client-secret-0091",
                        "signing_secret": "synthetic-slack-signing-secret-0091",
                    },
                },
            ),
            200,
            "OpenBao Slack client failed.",
        )
        policy = (
            'path "signal-slack/data/client" { capabilities = ["read"] }\n'
            'path "signal-slack/data/bots/*" { capabilities = ["create", "read"] }\n'
            'path "signal-slack/metadata/bots/*" { capabilities = ["delete"] }\n'
        )
        _expect(
            root.put("/v1/sys/policies/acl/signal-slack-connector", json={"policy": policy}),
            204,
            "OpenBao Slack policy failed.",
        )
        slack_token = _create_scoped_token(root, "signal-slack-connector")
    email_reader = OpenBaoSmtpCredential(server.base_url, email_token)
    email_credential = await email_reader.read(verify=server.tls_context)
    if email_credential.password != "synthetic-smtp-password":
        raise LabError("Email credential read failed.")
    checks.append({"name": "SMTP credential exact TLS OpenBao read", "status": "PASS"})
    for method, path in [
        ("POST", "/signal-email/data/smtp/default"),
        ("GET", "/signal-email/data/smtp/other"),
        ("GET", "/signal-slack/data/client"),
    ]:
        denied = await openbao_request(
            base_url=server.base_url,
            token=email_token,
            method=method,
            path=path,
            verify=server.tls_context,
        )
        if denied.status_code != 403:
            raise LabError("SMTP reader exceeded its single-secret read policy.")
    checks.append(
        {
            "name": "SMTP reader denies mutation other secrets and cross-provider read",
            "status": "PASS",
        }
    )
    slack = OpenBaoSlackSecrets(server.base_url, slack_token)
    await slack.client(verify=server.tls_context)
    denied = await openbao_request(
        base_url=server.base_url,
        token=slack_token,
        method="POST",
        path="/signal-slack/data/client",
        payload={"options": {"cas": 1}, "data": {}},
        verify=server.tls_context,
    )
    if denied.status_code != 403:
        raise LabError("Slack client/signing secret writer isolation failed.")
    checks.append({"name": "Slack OAuth and signing secrets read-only ACL", "status": "PASS"})
    identifier = uuid4()
    reference = await slack.store_bot(
        identifier, "synthetic-slack-bot-token-0091", verify=server.tls_context
    )
    if await slack.bot(reference, verify=server.tls_context) != "synthetic-slack-bot-token-0091":
        raise LabError("Slack bot reference changed.")
    try:
        await slack.store_bot(
            identifier, "synthetic-slack-replacement-token-0091", verify=server.tls_context
        )
    except SlackRejected:
        pass
    else:
        raise LabError("Slack CAS-zero overwrite was accepted.")
    checks.append({"name": "Slack bot CAS-zero immutable secret creation", "status": "PASS"})
    await slack.destroy_bot(reference, verify=server.tls_context)
    try:
        await slack.bot(reference, verify=server.tls_context)
    except SlackRejected:
        pass
    else:
        raise LabError("Destroyed Slack bot credential remains readable.")
    checks.append(
        {"name": "Slack bot permanent revocation and missing-secret denial", "status": "PASS"}
    )
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token},
    ) as root:
        _expect(
            mount_kv_v2(root, "signal-telegram"),
            204,
            "OpenBao Telegram mount failed.",
        )
        _expect(
            root.post("/v1/signal-telegram/config", json={"max_versions": 1, "cas_required": True}),
            204,
            "OpenBao Telegram configuration failed.",
        )
        policy = (
            'path "signal-telegram/data/bots/*" { capabilities = ["create", "read"] }\n'
            'path "signal-telegram/metadata/bots/*" { capabilities = ["delete"] }\n'
        )
        _expect(
            root.put("/v1/sys/policies/acl/signal-telegram-connector", json={"policy": policy}),
            204,
            "OpenBao Telegram policy failed.",
        )
        telegram_token = _create_scoped_token(root, "signal-telegram-connector")
    telegram = OpenBaoTelegramSecrets(server.base_url, telegram_token)
    identifier = uuid4()
    reference = await telegram.store_bot(
        identifier,
        "synthetic-telegram-bot-token-0092",
        "synthetic-telegram-webhook-secret-0092",
        verify=server.tls_context,
    )
    observed = await telegram.bot(reference, verify=server.tls_context)
    if observed != {
        "bot_token": "synthetic-telegram-bot-token-0092",
        "webhook_secret": "synthetic-telegram-webhook-secret-0092",
    }:
        raise LabError("Telegram bot or webhook secret reference changed.")
    denied = await openbao_request(
        base_url=server.base_url,
        token=telegram_token,
        method="GET",
        path="/signal-slack/data/client",
        verify=server.tls_context,
    )
    if denied.status_code != 403:
        raise LabError("Telegram cross-provider secret isolation failed.")
    checks.append(
        {"name": "Telegram bot and webhook secret isolated OpenBao ACL", "status": "PASS"}
    )
    try:
        await telegram.store_bot(
            identifier,
            "synthetic-telegram-replacement-token-0092",
            "synthetic-telegram-replacement-secret-0092",
            verify=server.tls_context,
        )
    except TelegramRejected:
        pass
    else:
        raise LabError("Telegram CAS-zero overwrite was accepted.")
    checks.append({"name": "Telegram CAS-zero immutable secret creation", "status": "PASS"})
    await telegram.destroy_bot(reference, verify=server.tls_context)
    try:
        await telegram.bot(reference, verify=server.tls_context)
    except TelegramRejected:
        pass
    else:
        raise LabError("Destroyed Telegram credentials remain readable.")
    checks.append(
        {"name": "Telegram permanent revocation and missing-secret denial", "status": "PASS"}
    )
    binding_id = uuid4()
    wordpress_scope = dict(tenant_id=uuid4(), site_id=uuid4(), origin="https://cms.example.invalid")
    wp_path = f"signal-wordpress/data/bindings/{binding_id}"
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token},
    ) as root:
        _expect(
            mount_kv_v2(root, "signal-wordpress"),
            204,
            "WordPress mount failed.",
        )
        _expect(
            root.post(
                "/v1/" + wp_path,
                json={
                    "data": {
                        "username": "synthetic-author",
                        "password": "synthetic-wordpress-application-password",
                        **{k: str(v) for k, v in wordpress_scope.items()},
                    }
                },
            ),
            200,
            "WordPress credential provisioning failed.",
        )
        _expect(
            root.put(
                "/v1/sys/policies/acl/signal-wordpress-reader",
                json={"policy": f'path "{wp_path}" {{ capabilities = ["read"] }}'},
            ),
            204,
            "WordPress reader policy failed.",
        )
        wordpress_token = _create_scoped_token(root, "signal-wordpress-reader")
    wordpress = OpenBaoWordPressSecrets(server.base_url, wordpress_token)
    credential = await wordpress.credential(
        binding_id, **wordpress_scope, verify=server.tls_context
    )
    if credential.password != "synthetic-wordpress-application-password":
        raise LabError("WordPress credential changed.")
    for method, path in [
        ("POST", "/" + wp_path),
        ("DELETE", "/" + wp_path),
        ("GET", f"/signal-wordpress/data/bindings/{uuid4()}"),
    ]:
        denied = await openbao_request(
            base_url=server.base_url,
            token=wordpress_token,
            method=method,
            path=path,
            verify=server.tls_context,
        )
        if denied.status_code != 403:
            raise LabError("WordPress reader exceeded its single-binding read scope.")
    checks.append(
        {"name": "WordPress application password single-binding read-only ACL", "status": "PASS"}
    )
    try:
        await wordpress.credential(uuid4(), **wordpress_scope, verify=server.tls_context)
    except WordPressUnavailable as error:
        if str(error) != "WORDPRESS_CREDENTIAL_UNAVAILABLE":
            raise LabError("WordPress credential error disclosed provider detail.") from None
    else:
        raise LabError("Unprovisioned WordPress credential was accepted.")
    checks.append(
        {
            "name": "WordPress missing credential fails closed without secret detail",
            "status": "PASS",
        }
    )
    for mismatch in [
        dict(tenant_id=uuid4()),
        dict(site_id=uuid4()),
        dict(origin="https://other.example.invalid"),
    ]:
        try:
            await wordpress.credential(
                binding_id, **{**wordpress_scope, **mismatch}, verify=server.tls_context
            )
        except WordPressUnavailable as error:
            if str(error) != "WORDPRESS_CREDENTIAL_UNAVAILABLE":
                raise LabError("WordPress assignment error disclosed credential detail.") from None
        else:
            raise LabError("WordPress credential crossed its tenant/site/origin assignment.")
    checks.append(
        {"name": "WordPress operator credential assignment precedes provider I/O", "status": "PASS"}
    )
    synthetic_model_key = "sk-local-pilot-model-credential-00000000"
    model_credential = provision_model_credential(server, synthetic_model_key)
    observed_model_key = await model_credential.api_key(verify=server.tls_context)
    if not secrets.compare_digest(observed_model_key, synthetic_model_key):
        raise LabError("OpenBao returned a different synthetic model credential.")
    denied_model_write = await openbao_request(
        base_url=model_credential.base_url,
        token=model_credential.token,
        method="POST",
        path=f"/{model_credential.mount}/data/openai/default",
        payload={"options": {"cas": 1}, "data": {"api_key": synthetic_model_key}},
        verify=server.tls_context,
    )
    if denied_model_write.status_code != 403:
        raise LabError("OpenBao model reader unexpectedly changed credential state.")
    checks.append({"name": "single-secret model reader rejects mutation", "status": "PASS"})
    artifact_key = provision_brand_artifact_key(server)
    observed_brand_key = await artifact_key.read(verify=server.tls_context)
    if not secrets.compare_digest(observed_brand_key.material, bytes(range(32))):
        raise LabError("OpenBao returned a different synthetic artifact key.")
    denied_artifact_write = await openbao_request(
        base_url=artifact_key.base_url,
        token=artifact_key.token,
        method="POST",
        path=f"/{artifact_key.mount}/data/brand/default",
        payload={"options": {"cas": 1}, "data": {"key_base64": "denied"}},
        verify=server.tls_context,
    )
    if denied_artifact_write.status_code != 403:
        raise LabError("OpenBao artifact reader unexpectedly changed key state.")
    checks.append({"name": "brand artifact key read-only isolation", "status": "PASS"})
    assistant_credentials = provision_assistant_credentials(server)
    availability = await assistant_credentials.availability(verify=server.tls_context)
    if availability != {
        "openai": "configured_internal_only",
        "perplexity": "configured_internal_only",
        "gemini": "unavailable",
    }:
        raise LabError("Independent assistant availability changed.")
    checks.append(
        {"name": "independent assistant availability and missing provider", "status": "PASS"}
    )
    observed_assistant_key = await assistant_credentials.api_key(
        "openai", verify=server.tls_context
    )
    if not secrets.compare_digest(observed_assistant_key, "synthetic-openai-key-00000000"):
        raise LabError("Assistant credential changed.")
    denied_assistant_write = await openbao_request(
        base_url=assistant_credentials.base_url,
        token=assistant_credentials.token,
        method="POST",
        path=f"/{ASSISTANT_MOUNT}/data/openai/default",
        payload={"options": {"cas": 1}, "data": {"api_key": observed_assistant_key}},
        verify=server.tls_context,
    )
    if denied_assistant_write.status_code != 403:
        raise LabError("Assistant reader unexpectedly changed credential state.")
    checks.append({"name": "assistant reader rejects mutation", "status": "PASS"})
    tenant, site, credential_generation = uuid4(), uuid4(), uuid4()
    mount = "signal-dataforseo"
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token},
    ) as root:
        _expect(
            mount_kv_v2(root, mount),
            204,
            "DataForSEO mount failed.",
        )
        _expect(
            root.post(f"/v1/{mount}/config", json={"max_versions": 1, "cas_required": True}),
            204,
            "DataForSEO KV configuration failed.",
        )
        for name, capabilities in (
            ("synthetic-dataforseo-writer", '["create","read"]'),
            ("synthetic-dataforseo-reader", '["read"]'),
        ):
            policy = f'path "{mount}/data/{tenant}/{site}/*" {{ capabilities = {capabilities} }}\n'
            if name.endswith("writer"):
                policy += (
                    f'path "{mount}/metadata/{tenant}/{site}/*" {{ capabilities = ["delete"] }}\n'
                )
            _expect(
                root.put(f"/v1/sys/policies/acl/{name}", json={"policy": policy}),
                204,
                "DataForSEO policy failed.",
            )
        dfs_writer = OpenBaoDataForSeoCredentials(
            server.base_url, _create_scoped_token(root, "synthetic-dataforseo-writer")
        )
        dfs_reader = OpenBaoDataForSeoCredentials(
            server.base_url, _create_scoped_token(root, "synthetic-dataforseo-reader")
        )
    credential_args = (tenant, site, credential_generation)
    await dfs_writer.put(
        *credential_args,
        "synthetic-login@example.invalid",
        "synthetic-dataforseo-password",
        verify=server.tls_context,
    )
    credential_scope = await dfs_reader.scope(*credential_args, verify=server.tls_context)
    if "synthetic-dataforseo-password" in repr(credential_scope):
        raise LabError("DataForSEO credential repr exposed a secret.")
    for operation in (
        dfs_reader.put(
            *credential_args,
            "synthetic-login@example.invalid",
            "synthetic-dataforseo-password",
            verify=server.tls_context,
        ),
        dfs_reader.remove(*credential_args, verify=server.tls_context),
        dfs_reader.scope(tenant, uuid4(), credential_generation, verify=server.tls_context),
        dfs_writer.put(
            *credential_args,
            "synthetic-login@example.invalid",
            "synthetic-dataforseo-password",
            verify=server.tls_context,
        ),
    ):
        try:
            await operation
        except DataForSeoUnavailable:
            pass
        else:
            raise LabError("DataForSEO secret scope or CAS denial failed.")
    await dfs_writer.remove(*credential_args, verify=server.tls_context)
    try:
        await dfs_reader.scope(*credential_args, verify=server.tls_context)
    except DataForSeoUnavailable:
        pass
    else:
        raise LabError("Removed DataForSEO secret remained available.")
    checks.append(
        {
            "name": "DataForSEO site-scoped KV CAS, reader denials and permanent removal",
            "status": "PASS",
        }
    )

    pagespeed_credentials = provision_pagespeed_credentials(server)
    observed_pagespeed_key = await pagespeed_credentials.api_key(verify=server.tls_context)
    if not secrets.compare_digest(observed_pagespeed_key, "synthetic-pagespeed-key-00000000"):
        raise LabError("PageSpeed credential changed.")
    checks.append({"name": "PageSpeed exact OpenBao key read", "status": "PASS"})
    denied_pagespeed_write = await openbao_request(
        base_url=pagespeed_credentials.base_url,
        token=pagespeed_credentials.token,
        method="POST",
        path="/" + pagespeed_credentials.secret_path,
        payload={"options": {"cas": 1}, "data": {"api_key": "synthetic-denied-key-00000000"}},
        verify=server.tls_context,
    )
    if denied_pagespeed_write.status_code != 403:
        raise LabError("PageSpeed reader unexpectedly changed credential state.")
    checks.append({"name": "PageSpeed reader rejects key mutation", "status": "PASS"})
    missing_pagespeed = OpenBaoPageSpeedCredentials(
        server.base_url, pagespeed_credentials.token, "signal-pagespeed/data/pagespeed/other"
    )
    try:
        await missing_pagespeed.api_key(verify=server.tls_context)
    except PageSpeedCredentialUnavailable:
        checks.append(
            {"name": "PageSpeed other key path denied without keyless fallback", "status": "PASS"}
        )
    else:
        raise LabError("PageSpeed reader unexpectedly read another key path.")
    keyless_pagespeed = OpenBaoPageSpeedCredentials(server.base_url, pagespeed_credentials.token)
    if await keyless_pagespeed.api_key(verify=server.tls_context) is not None:
        raise LabError("Unconfigured PageSpeed key was not keyless.")
    checks.append({"name": "PageSpeed optional keyless configuration", "status": "PASS"})
    synthetic_jev_key = "typesafe-local-decision-credential-00000000"
    jev_credential = provision_jev_credential(server, synthetic_jev_key)
    observed_jev_key = await jev_credential.api_key(verify=server.tls_context)
    if not secrets.compare_digest(observed_jev_key, synthetic_jev_key):
        raise LabError("OpenBao returned a different synthetic Jev credential.")
    denied_jev_write = await openbao_request(
        base_url=jev_credential.base_url,
        token=jev_credential.token,
        method="POST",
        path=f"/{jev_credential.mount}/data/typesafe/default",
        payload={"options": {"cas": 1}, "data": {"api_key": synthetic_jev_key}},
        verify=server.tls_context,
    )
    if denied_jev_write.status_code != 403:
        raise LabError("OpenBao Jev reader unexpectedly changed credential state.")
    checks.append({"name": "single-secret Jev reader rejects mutation", "status": "PASS"})
    private_key_pem = (
        RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
        .as_pem(private=True)
        .decode("ascii")
    )
    github_credential = provision_github_credential(server, private_key_pem)
    observed_github = await github_credential.credentials(verify=server.tls_context)
    if observed_github.app_id != 12345 or not secrets.compare_digest(
        observed_github.private_key_pem, private_key_pem
    ):
        raise LabError("OpenBao returned a different synthetic GitHub App credential.")
    denied_github_write = await openbao_request(
        base_url=github_credential.base_url,
        token=github_credential.token,
        method="POST",
        path=f"/{github_credential.mount}/data/github/app",
        payload={
            "options": {"cas": 1},
            "data": {
                "app_id": 12345,
                "private_key_pem": private_key_pem,
            },
        },
        verify=server.tls_context,
    )
    if denied_github_write.status_code != 403:
        raise LabError("OpenBao GitHub reader unexpectedly changed credential state.")
    checks.append({"name": "single-secret GitHub App reader rejects mutation", "status": "PASS"})
    gsc_secrets = provision_gsc_secrets(server)
    client = await gsc_secrets.client_credentials(verify=server.tls_context)
    if client.client_id != "synthetic-client-123.apps.googleusercontent.com":
        raise LabError("OpenBao GSC client identity changed.")
    denied_client_write = await openbao_request(
        base_url=gsc_secrets.base_url,
        token=gsc_secrets.token,
        method="POST",
        path=f"/{GSC_MOUNT}/data/oauth-client",
        payload={"options": {"cas": 1}, "data": {"client_id": client.client_id}},
        verify=server.tls_context,
    )
    if denied_client_write.status_code != 403:
        raise LabError("OpenBao GSC connector changed OAuth client credentials.")
    checks.append({"name": "GSC OAuth client secret is read-only", "status": "PASS"})
    gsc_attempt = uuid4()
    gsc_verifier = secrets.token_urlsafe(64)
    await gsc_secrets.store_verifier(gsc_attempt, gsc_verifier, verify=server.tls_context)
    observed_verifier = await gsc_secrets.consume_verifier(gsc_attempt, verify=server.tls_context)
    if not secrets.compare_digest(observed_verifier, gsc_verifier):
        raise LabError("OpenBao GSC PKCE verifier changed.")
    try:
        await gsc_secrets.consume_verifier(gsc_attempt, verify=server.tls_context)
    except GscSecretError:
        pass
    else:
        raise LabError("Consumed GSC verifier remained readable.")
    checks.append({"name": "GSC PKCE verifier permanently consumed", "status": "PASS"})
    gsc_refresh = "synthetic-refresh-token-not-a-real-secret"
    refresh_reference = await gsc_secrets.store_refresh_token(
        uuid4(), gsc_refresh, verify=server.tls_context
    )
    observed_refresh = await gsc_secrets.refresh_token(refresh_reference, verify=server.tls_context)
    if not secrets.compare_digest(observed_refresh, gsc_refresh):
        raise LabError("OpenBao GSC refresh token changed.")
    rotated_refresh = "synthetic-rotated-refresh-token-not-real"
    await gsc_secrets.replace_refresh_token(
        refresh_reference, gsc_refresh, rotated_refresh, verify=server.tls_context
    )
    observed_rotated = await gsc_secrets.refresh_token(refresh_reference, verify=server.tls_context)
    if not secrets.compare_digest(observed_rotated, rotated_refresh):
        raise LabError("OpenBao GSC CAS rotation was not durable.")
    checks.append({"name": "GSC refresh rotation uses OpenBao CAS", "status": "PASS"})
    await gsc_secrets.destroy_refresh_token(refresh_reference, verify=server.tls_context)
    try:
        await gsc_secrets.refresh_token(refresh_reference, verify=server.tls_context)
    except GscSecretError:
        pass
    else:
        raise LabError("Destroyed GSC refresh token remained readable.")
    checks.append({"name": "GSC refresh token stays in OpenBao and is destroyed", "status": "PASS"})
    ga4 = provision_gsc_secrets(server, "signal-ga4", "signal-ga4-connector", OpenBaoGa4Secrets)
    await ga4.client_credentials(verify=server.tls_context)
    attempt = uuid4()
    verifier = secrets.token_urlsafe(64)
    await ga4.store_verifier(attempt, verifier, verify=server.tls_context)
    if await ga4.consume_verifier(attempt, verify=server.tls_context) != verifier:
        raise LabError("GA4 verifier changed.")
    try:
        await ga4.consume_verifier(attempt, verify=server.tls_context)
    except GscSecretError:
        pass
    else:
        raise LabError("GA4 verifier replay allowed.")
    checks.append({"name": "GA4 isolated PKCE permanently consumed", "status": "PASS"})
    reference = await ga4.store_refresh_token(
        attempt, "synthetic-ga4-refresh-secret", verify=server.tls_context
    )
    await ga4.replace_refresh_token(
        reference,
        "synthetic-ga4-refresh-secret",
        "synthetic-ga4-rotated-secret",
        verify=server.tls_context,
    )
    if (
        await ga4.refresh_token(reference, verify=server.tls_context)
        != "synthetic-ga4-rotated-secret"
    ):
        raise LabError("GA4 CAS rotation lost.")
    checks.append({"name": "GA4 refresh uses isolated OpenBao CAS", "status": "PASS"})
    denied = await openbao_request(
        base_url=server.base_url,
        token=ga4.token,
        method="GET",
        path=f"/{GSC_MOUNT}/data/oauth-client",
        verify=server.tls_context,
    )
    if denied.status_code != 403:
        raise LabError("GA4 token crossed the GSC namespace.")
    checks.append({"name": "GA4 OpenBao ACL denies GSC namespace", "status": "PASS"})
    await ga4.destroy_refresh_token(reference, verify=server.tls_context)
    try:
        await ga4.refresh_token(reference, verify=server.tls_context)
    except GscSecretError:
        pass
    else:
        raise LabError("GA4 destroyed refresh token readable.")
    checks.append({"name": "GA4 refresh secret permanently destroyed", "status": "PASS"})
    docs = provision_gsc_secrets(
        server,
        mount="signal-google-docs",
        policy_name="signal-google-docs-connector",
        store_type=OpenBaoDocsSecrets,
    )
    await docs.client_credentials(verify=server.tls_context)
    for path, method in (
        ("signal-google-docs/data/oauth-client", "POST"),
        ("signal-gsc/data/oauth-client", "GET"),
    ):
        denied = await openbao_request(
            base_url=docs.base_url,
            token=docs.token,
            method=method,
            path="/" + path,
            payload={"options": {"cas": 1}, "data": {}} if method == "POST" else None,
            verify=server.tls_context,
        )
        if denied.status_code != 403:
            raise LabError("Google Docs client ACL or mount isolation failed.")
    checks.append({"name": "Google Docs client read-only ACL and GSC isolation", "status": "PASS"})
    attempt = uuid4()
    verifier = secrets.token_urlsafe(64)
    await docs.store_verifier(attempt, verifier, verify=server.tls_context)
    assert await docs.consume_verifier(attempt, verify=server.tls_context) == verifier
    try:
        await docs.consume_verifier(attempt, verify=server.tls_context)
    except GscSecretError:
        pass
    else:
        raise LabError("Google Docs verifier replay was accepted.")
    checks.append({"name": "Google Docs PKCE permanent consume", "status": "PASS"})
    old = "synthetic-docs-refresh-token-not-real"
    new = "synthetic-docs-rotated-refresh-not-real"
    reference = await docs.store_refresh_token(attempt, old, verify=server.tls_context)
    assert reference == f"secret://google-docs/{attempt}"
    await docs.replace_refresh_token(reference, old, new, verify=server.tls_context)
    assert await docs.refresh_token(reference, verify=server.tls_context) == new
    checks.append({"name": "Google Docs refresh CAS rotation", "status": "PASS"})
    await docs.destroy_refresh_token(reference, verify=server.tls_context)
    try:
        await docs.refresh_token(reference, verify=server.tls_context)
    except GscSecretError:
        pass
    else:
        raise LabError("Google Docs refresh deletion failed.")
    checks.append({"name": "Google Docs refresh permanently destroyed", "status": "PASS"})
    bing_secrets = provision_bing_secrets(server)
    bing_client = await bing_secrets.client_credentials(verify=server.tls_context)
    if bing_client.client_id != "synthetic-bing-client-123456":
        raise LabError("OpenBao Bing client identity changed.")
    denied_bing_write = await openbao_request(
        base_url=bing_secrets.base_url,
        token=bing_secrets.token,
        method="POST",
        path=f"/{BING_MOUNT}/data/oauth-client",
        payload={"options": {"cas": 1}, "data": {"client_id": bing_client.client_id}},
        verify=server.tls_context,
    )
    if denied_bing_write.status_code != 403:
        raise LabError("OpenBao Bing connector changed OAuth client credentials.")
    checks.append({"name": "Bing OAuth client secret is read-only", "status": "PASS"})
    bing_refresh = "synthetic-bing-refresh-token-not-real"
    bing_reference = await bing_secrets.store_refresh_token(
        uuid4(), bing_refresh, verify=server.tls_context
    )
    observed_bing_refresh = await bing_secrets.refresh_token(
        bing_reference, verify=server.tls_context
    )
    if not secrets.compare_digest(observed_bing_refresh, bing_refresh):
        raise LabError("OpenBao Bing refresh token changed.")
    rotated_bing_refresh = "synthetic-bing-rotated-refresh-token"
    await bing_secrets.replace_refresh_token(
        bing_reference, bing_refresh, rotated_bing_refresh, verify=server.tls_context
    )
    if not secrets.compare_digest(
        await bing_secrets.refresh_token(bing_reference, verify=server.tls_context),
        rotated_bing_refresh,
    ):
        raise LabError("OpenBao Bing CAS rotation was not durable.")
    checks.append({"name": "Bing refresh rotation uses OpenBao CAS", "status": "PASS"})
    await bing_secrets.destroy_refresh_token(bing_reference, verify=server.tls_context)
    try:
        await bing_secrets.refresh_token(bing_reference, verify=server.tls_context)
    except BingSecretError:
        pass
    else:
        raise LabError("Destroyed Bing refresh token remained readable.")
    checks.append(
        {"name": "Bing refresh token stays in OpenBao and is destroyed", "status": "PASS"}
    )
    observed_generation = await recovery_authority.current_generation(verify=server.tls_context)
    if observed_generation.version != 1 or not secrets.compare_digest(
        observed_generation.value, expected_generation
    ):
        raise LabError("OpenBao recovery authority returned an unexpected generation.")
    checks.append({"name": "read-only external recovery generation authority", "status": "PASS"})
    denied_write = await openbao_request(
        base_url=recovery_authority.base_url,
        token=recovery_authority.token,
        method="POST",
        path=f"/{recovery_authority.mount}/data/recovery/current",
        payload={
            "options": {"cas": observed_generation.version},
            "data": {"generation": "unauthorized-generation"},
        },
        verify=server.tls_context,
    )
    if denied_write.status_code != 403:
        raise LabError("OpenBao recovery reader unexpectedly changed authority state.")
    checks.append({"name": "recovery authority rejects reader mutation", "status": "PASS"})
    first_id = uuid4()
    first_verifier = secrets.token_urlsafe(64)
    first_reference = await writer.store_verifier(
        attempt_id=first_id,
        code_verifier=first_verifier,
        verify=server.tls_context,
    )
    checks.append({"name": "CAS-zero PKCE verifier creation", "status": "PASS"})

    try:
        await writer.consume_verifier(
            secret_reference=first_reference,
            verify=server.tls_context,
        )
    except PkceSecretError as error:
        if error.code != "PKCE_SECRET_READ_FAILED":
            raise
    else:
        raise LabError("OpenBao writer credential unexpectedly read a PKCE verifier.")
    try:
        await consumer.store_verifier(
            attempt_id=uuid4(),
            code_verifier=secrets.token_urlsafe(64),
            verify=server.tls_context,
        )
    except PkceSecretError as error:
        if error.code != "PKCE_SECRET_WRITE_FAILED":
            raise
    else:
        raise LabError("OpenBao consumer credential unexpectedly wrote a PKCE verifier.")
    checks.append({"name": "separate writer and consumer ACLs", "status": "PASS"})

    consumed = await consumer.consume_verifier(
        secret_reference=first_reference,
        verify=server.tls_context,
    )
    if not secrets.compare_digest(consumed, first_verifier):
        raise LabError("OpenBao returned a different PKCE verifier.")
    try:
        await consumer.consume_verifier(
            secret_reference=first_reference,
            verify=server.tls_context,
        )
    except PkceSecretUnavailable:
        pass
    else:
        raise LabError("A permanently consumed PKCE verifier remained readable.")
    checks.append({"name": "confirmed permanent consume and replay rejection", "status": "PASS"})

    conflict_id = uuid4()
    original = secrets.token_urlsafe(64)
    conflict_reference = await writer.store_verifier(
        attempt_id=conflict_id,
        code_verifier=original,
        verify=server.tls_context,
    )
    try:
        await writer.store_verifier(
            attempt_id=conflict_id,
            code_verifier=secrets.token_urlsafe(64),
            verify=server.tls_context,
        )
    except PkceSecretError as error:
        if error.code not in {"PKCE_SECRET_CONFLICT", "PKCE_SECRET_WRITE_FAILED"}:
            raise
    else:
        raise LabError("OpenBao allowed a PKCE verifier overwrite.")
    preserved = await consumer.consume_verifier(
        secret_reference=conflict_reference,
        verify=server.tls_context,
    )
    if not secrets.compare_digest(preserved, original):
        raise LabError("CAS conflict changed the original PKCE verifier.")
    checks.append(
        {"name": "conflicting overwrite rejected and original preserved", "status": "PASS"}
    )
    return checks


def source_hashes() -> dict[str, str]:
    files = [
        ROOT / path
        for path in [
            "requirements.txt",
            "scripts/openbao_lab.py",
            "scripts/lab_runtime.py",
            "services/control_plane/src/signal_core/model_credentials.py",
            "services/control_plane/src/signal_core/artifact_keys.py",
            "services/control_plane/src/signal_core/assistant_credentials.py",
            "services/control_plane/src/signal_core/dataforseo_credentials.py",
            "services/control_plane/src/signal_core/pagespeed_credentials.py",
            "services/control_plane/src/signal_core/gsc_secrets.py",
            "services/control_plane/src/signal_core/connector_secrets.py",
            "services/control_plane/src/signal_core/ga4_secrets.py",
            "services/control_plane/src/signal_core/bing_secrets.py",
            "services/control_plane/src/signal_core/slack_secrets.py",
            "services/control_plane/src/signal_core/slack_protocol.py",
            "services/control_plane/src/signal_core/indexnow_secrets.py",
            "services/control_plane/src/signal_core/indexnow_protocol.py",
            "services/control_plane/src/signal_core/smtp_submission.py",
            "services/control_plane/src/signal_core/telegram_secrets.py",
            "services/control_plane/src/signal_core/telegram_protocol.py",
            "services/control_plane/src/signal_core/openbao_http.py",
            "services/control_plane/src/signal_core/pkce_secrets.py",
            "services/control_plane/src/signal_core/recovery_authority.py",
            "tests/identity/test_pkce_secrets.py",
            "tests/identity/test_recovery_authority.py",
            "tests/identity/test_model_credentials.py",
            "tests/identity/test_artifact_keys.py",
            "tests/tooling/test_openbao_lab.py",
        ]
    ]
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }


def _create_scoped_token(root: httpx2.Client, policy: str) -> str:
    response = _expect(
        root.post(
            "/v1/auth/token/create",
            json={
                "policies": [policy],
                "no_default_policy": True,
                "renewable": False,
                "ttl": "15m",
                "explicit_max_ttl": "15m",
            },
        ),
        200,
        "OpenBao scoped-token creation failed.",
    )
    document = _document(response, "OpenBao scoped-token creation failed.")
    auth = document.get("auth")
    token = auth.get("client_token") if isinstance(auth, dict) else None
    if not isinstance(token, str) or not 16 <= len(token) <= 4096:
        raise LabError("OpenBao scoped-token response was invalid.")
    return token


def _expect(response: httpx2.Response, status: int, message: str) -> httpx2.Response:
    if response.status_code != status:
        raise LabError(message)
    return response


def _document(response: httpx2.Response, message: str) -> dict:
    if len(response.content) > 16 * 1024:
        raise LabError(message)
    try:
        document = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise LabError(message) from None
    if not isinstance(document, dict):
        raise LabError(message)
    return document


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(content)


def main() -> int:
    hashes = source_hashes()
    try:
        with isolated_openbao() as server:
            writer, consumer, recovery_authority, generation = provision(server)
            checks = asyncio.run(
                run_checks(server, writer, consumer, recovery_authority, generation)
            )
            version = server.version
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
            "synthetic_secrets_only": True,
            "token_material_recorded": False,
            "secret_material_recorded": False,
            "recovery_generation_recorded": False,
            "transport_security": "OpenBao-generated locally trusted TLS",
            "cleanup": "completed",
        }
        directory = runtime_root(ROOT) / "openbao-tests"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
        return 0
    except LabError as error:
        print(str(error), file=sys.stderr)
    except PkceSecretError as error:
        print(f"OpenBao PKCE qualification failed ({error.code}).", file=sys.stderr)
    except RecoveryAuthorityError as error:
        print(f"OpenBao recovery qualification failed ({error.code}).", file=sys.stderr)
    except (httpx2.HTTPError, OSError, ValueError) as error:
        print(f"OpenBao lab failed safely ({type(error).__name__}).", file=sys.stderr)
    except Exception as error:
        print(f"OpenBao lab failed safely ({type(error).__name__}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
