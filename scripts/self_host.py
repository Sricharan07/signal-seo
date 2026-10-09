"""Owner-operated self-host bootstrap. Never print credentials or write them to Git."""

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import time
import warnings
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import httpx2
from integration_application import POLICIES, SELF
from integration_connector_secrets import wait_for_new_mount
from integration_secrets import (
    OperatorError,
    Store,
    check_audit,
    private_directory,
    read_private,
    snapshot,
    write_private,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.self_host_config import (  # noqa: E402
    PROVIDERS,
    SelfHostConfig,
    provider_configuration,
    provider_projection,
    unique_object,
)

INSTALL = "/signal-self-host/data/install"
APPROVAL = "/signal-self-host/data/owner"
INITIAL_PASSWORD = "/signal-self-host/data/owner-initial-password"
RELEASE = "/signal-self-host/data/release"
PREFIX = "signal-self-host-"
LIMIT = 65536
PASSWORDS = (
    "postgres",
    "signal_migrator",
    "signal_identity",
    "signal_scheduler",
    "signal_workflow",
    "identity_postgres",
    "keycloak",
    "keycloak_bootstrap",
    "temporal_postgres",
    "temporal",
)


def external_path(path):
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise OperatorError("Use an absolute nonsymlink path outside every repository.")
    resolved = path.resolve()
    if (
        resolved == ROOT
        or ROOT in resolved.parents
        or any((parent / ".git").exists() for parent in resolved.parents)
    ):
        raise OperatorError("Private material must stay outside every repository.")
    return resolved


def runtime_directory(path):
    path = external_path(path)
    if sys.platform != "linux" or os.geteuid() != 0:
        raise OperatorError("Runtime hydration requires the root operator on a Linux host.")
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    matching = []
    for line in mounts:
        before, after = line.split(" - ", 1)
        mount = Path(before.split()[4])
        if path == mount or mount in path.parents:
            matching.append((len(mount.parts), after.split()[0]))
    if not matching or max(matching)[1] != "tmpfs":
        raise OperatorError("Runtime secrets require a verified Linux tmpfs mount.")
    return private_directory(path)


def hidden(prompt):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise OperatorError("Secret entry requires a non-echoed interactive terminal.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(prompt + " (hidden): ")


def configuration(path):
    return SelfHostConfig.parse(
        json.loads(read_private(external_path(path)), object_pairs_hook=unique_object)
    )


def run(command, *, env=None, content=None, timeout=180):
    try:
        result = subprocess.run(
            command, input=content, capture_output=True, timeout=timeout, check=True, env=env
        )
        if len(result.stdout) > 1024 * 1024:
            raise OperatorError("Operator response exceeded its bound.")
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        raise OperatorError("Private operator command failed; reconcile before retrying.") from None


class Compose:
    def __init__(self, config, runtime):
        if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
            raise OperatorError("Remote Docker overrides are forbidden.")
        endpoint = json.loads(run(["docker", "context", "inspect"]))[0]["Endpoints"]["docker"][
            "Host"
        ]
        if not endpoint.startswith("unix://"):
            raise OperatorError("Only a local Unix-socket Docker engine is allowed.")
        self.command = [
            "docker",
            "compose",
            "--project-name",
            config.project,
            "--project-directory",
            str(ROOT / "deploy/self-host"),
            "--env-file",
            "/dev/null",
            "--profile",
            "operator",
            "-f",
            str(ROOT / "deploy/self-host/compose.yaml"),
        ]
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("SIGNAL_", "COMPOSE_"))
        }
        self.env.update(
            SIGNAL_SELF_HOST_PROJECT=config.project,
            SIGNAL_SELF_HOST_RUNTIME=str(runtime),
            SIGNAL_SELF_HOST_ORIGIN=config.origin,
        )
        self.env.update(
            {"SIGNAL_" + name.upper() + "_IMAGE": value for name, value in config.images.items()}
        )
        normalized = json.loads(self.call("config", "--format", "json"))
        validate_compose(normalized, config)
        self.volumes = normalized["volumes"]

    def call(self, *args, content=None, timeout=180):
        return run(self.command + list(args), env=self.env, content=content, timeout=timeout)

    def ensure_volumes(self):
        engine_root = external_path(
            Path(run(["docker", "info", "--format", "{{.DockerRootDir}}"]).decode().strip())
        )
        owners = {
            "bao-data": (100, 1000),
            "bao-audit": (100, 1000),
            "application-data": (70, 70),
            "identity-data": (70, 70),
            "temporal-data": (70, 70),
        }
        existing = run(["docker", "volume", "ls", "--format", "{{.Name}}"]).decode().splitlines()
        for logical, spec in self.volumes.items():
            name = spec["name"]
            if name not in existing:
                run(
                    [
                        "docker",
                        "volume",
                        "create",
                        "--label",
                        "com.docker.compose.project=" + self.env["SIGNAL_SELF_HOST_PROJECT"],
                        "--label",
                        "com.docker.compose.volume=" + logical,
                        name,
                    ]
                )
                fresh = True
            else:
                fresh = False
            item = json.loads(run(["docker", "volume", "inspect", name]))[0]
            labels = item.get("Labels") or {}
            if (
                item["Driver"] != "local"
                or item.get("Options")
                or labels.get("com.docker.compose.project") != self.env["SIGNAL_SELF_HOST_PROJECT"]
                or labels.get("com.docker.compose.volume") != logical
            ):
                raise OperatorError("Volume is not an invocation-selected local project volume.")
            path = external_path(Path(item["Mountpoint"]))
            if path != engine_root / "volumes" / name / "_data":
                raise OperatorError("Unexpected volume mountpoint; no host changes attempted.")
            if fresh:
                if any(path.iterdir()):
                    raise OperatorError("Fresh volume unexpectedly contains data.")
                os.chown(path, *owners[logical])
                os.chmod(path, 0o700)
            if (path.stat().st_uid, path.stat().st_gid) != owners[logical]:
                raise OperatorError("Existing volume ownership is unsafe; no repair attempted.")


def validate_compose(document, config):
    services = document.get("services", {})
    expected = {
        "ingress",
        "openbao",
        "application-database",
        "identity-database",
        "temporal-database",
        "identity",
        "temporal",
        "api",
        "dashboard",
        "workflow-consumer",
        "bootstrap-job",
        "temporal-schema",
    }
    if set(services) != expected or document.get("name") != config.project:
        raise OperatorError("Exact reviewed self-host topology required.")
    for name, service in services.items():
        if (
            not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", service.get("image", ""))
            or service.get("privileged")
            or service.get("network_mode")
            or service.get("pid")
            or service.get("build")
            or service.get("user", "").split(":")[0] in {"", "0", "root"}
            or service.get("read_only") is not True
            or service.get("cap_drop") != ["ALL"]
            or service.get("cap_add")
            or service.get("security_opt") != ["no-new-privileges:true"]
        ):
            raise OperatorError("Unpinned or unsafe container configuration rejected.")
        ports = service.get("ports", [])
        if name == "ingress":
            if (
                len(ports) != 1
                or str(ports[0].get("published")) != "443"
                or ports[0].get("target") != 8443
                or ports[0].get("protocol") != "tcp"
            ):
                raise OperatorError("Only HTTPS ingress may publish a port.")
        elif ports:
            raise OperatorError("Private services must not publish any ports.")
        for mount in service.get("volumes", []):
            if mount.get("type") == "bind" and (
                not mount.get("read_only") or mount.get("source") in {"/", "/var/run/docker.sock"}
            ):
                raise OperatorError("Writable or unrestricted host mounts rejected.")
    networks = document.get("networks", {})
    if any(
        name != "edge" and network.get("internal") is not True for name, network in networks.items()
    ):
        raise OperatorError("Private networks must have no Internet route.")
    if any(
        "edge" in service.get("networks", {})
        for name, service in services.items()
        if name != "ingress"
    ):
        raise OperatorError("Only the ingress may reach the edge network.")


def kv_read(store, token, path):
    response = store.request("GET", path, token=token, expected=(200, 404))
    if getattr(response, "status", 200) == 404:
        return None
    envelope = response.get("data", {})
    metadata = envelope.get("metadata", {})
    if (
        type(metadata.get("version")) is not int
        or metadata["version"] < 1
        or metadata.get("destroyed") is not False
        or metadata.get("deletion_time") not in {None, ""}
        or not isinstance(envelope.get("data"), dict)
    ):
        raise OperatorError("Secret generation is deleted, unknown or invalid.")
    return envelope["data"], metadata["version"]


def ensure_mount(store, token, mount, *, ephemeral=False):
    mounts = store.request("GET", "/sys/mounts", token=token)["data"]
    current = mounts.get(mount + "/")
    wanted = {
        "cas_required": True,
        "max_versions": 1 if ephemeral else 10,
        "delete_version_after": "10m" if ephemeral else "0s",
    }
    if current is None:
        store.request(
            "POST",
            "/sys/mounts/" + mount,
            token=token,
            payload={"type": "kv", "options": {"version": "2"}},
        )
        wait_for_new_mount(store, token, mount)
        store.request("POST", "/" + mount + "/config", token=token, payload=wanted)
    elif current.get("type") != "kv" or current.get("options", {}).get("version") != "2":
        raise OperatorError("Existing mount is incompatible; no replacement attempted.")
    actual = store.request("GET", "/" + mount + "/config", token=token)["data"]
    expiry = "10m0s" if ephemeral else "0s"
    if (
        actual.get("cas_required") is not True
        or actual.get("max_versions") != wanted["max_versions"]
        or actual.get("delete_version_after") not in {wanted["delete_version_after"], expiry}
    ):
        raise OperatorError("Existing mount safety settings differ; no repair attempted.")


def create_secret(store, token, path, data):
    existing = kv_read(store, token, path)
    if existing is not None:
        if existing != (data, 1):
            raise OperatorError("Existing secret differs; rotation must be reviewed separately.")
        return False
    store.request("POST", path, token=token, payload={"options": {"cas": 0}, "data": data})
    if kv_read(store, token, path) != (data, 1):
        raise OperatorError("Secret write outcome unknown; no automatic retry.")
    return True


def initialize(store, recovery_directory, pgp_keys, root_pgp_key):
    destination = recovery_directory / "openbao-init.pgp.json"
    pending = recovery_directory / "openbao-init.intent.json"
    state = store.request("GET", "/sys/init")
    if state.get("initialized") is True:
        if destination.exists():
            bundle = json.loads(read_private(destination), object_pairs_hook=unique_object)
            if (
                set(bundle) != {"keys_base64", "root_token", "secret_threshold", "secret_shares"}
                or bundle["secret_threshold"] != 2
                or bundle["secret_shares"] != 3
                or len(bundle["keys_base64"]) != 3
                or not all(isinstance(key, str) and key for key in bundle["keys_base64"])
                or not isinstance(bundle["root_token"], str)
                or not bundle["root_token"]
            ):
                raise OperatorError("Acknowledged recovery bundle is malformed.")
            return False
        raise OperatorError(
            "Initialized store has no acknowledged recovery bundle; stop and recover."
        )
    if state.get("initialized") is not False or pending.exists() or destination.exists():
        raise OperatorError("Initialization state is unsafe or unknown; no retry attempted.")
    if (
        len(pgp_keys) != 3
        or len(set(pgp_keys)) != 3
        or not root_pgp_key
        or any(not key for key in pgp_keys)
    ):
        raise OperatorError("Three distinct PGP recipients and a root recipient are required.")
    for key in (*pgp_keys, root_pgp_key):
        if not 32 <= len(base64.b64decode(key, validate=True)) <= 16384:
            raise OperatorError("Binary public PGP key rejected.")
    write_private(pending, b'{"status":"INIT_OUTCOME_UNKNOWN_UNTIL_ACKNOWLEDGED"}')
    response = store.request(
        "POST",
        "/sys/init",
        payload={
            "secret_shares": 3,
            "secret_threshold": 2,
            "pgp_keys": pgp_keys,
            "root_token_pgp_key": root_pgp_key,
        },
        timeout=90,
    )
    keys = response.get("keys_base64")
    if (
        not isinstance(keys, list)
        or len(keys) != 3
        or not isinstance(response.get("root_token"), str)
        or any(not isinstance(key, str) for key in keys)
    ):
        raise OperatorError("Initialization response rejected; preserve pending recovery state.")
    # OpenBao encrypts each share/root directly to its independently held PGP recipient.
    write_private(
        destination,
        json.dumps(
            {
                "keys_base64": keys,
                "root_token": response["root_token"],
                "secret_threshold": 2,
                "secret_shares": 3,
            }
        ).encode(),
    )
    return True


def unseal(store):
    state = store.request("GET", "/sys/seal-status")
    if state.get("n") != 3 or state.get("t") != 2:
        raise OperatorError("Expected three-share, two-share-threshold Shamir seal.")
    if state.get("sealed") is False:
        return False
    if state.get("sealed") is not True or state.get("progress") != 0:
        raise OperatorError("Existing unseal progress is unknown; do not mix recovery ceremonies.")
    for _ in range(2):
        store.request("POST", "/sys/unseal", payload={"key": hidden("Unseal share")}, timeout=90)
    if store.request("GET", "/sys/seal-status").get("sealed") is not False:
        raise OperatorError("Unseal did not reach the expected state.")
    return True


def provision(store, token, config, tls):
    check_audit(store, token)
    state = store.request("GET", "/sys/seal-status")
    if (
        state.get("sealed") is not False
        or state.get("n") != 3
        or state.get("t") != 2
        or not state.get("cluster_id")
    ):
        raise OperatorError("An unsealed identified store is required.")
    ensure_mount(store, token, "signal-self-host")
    existing = kv_read(store, token, INSTALL)
    public = asdict(config)
    if existing is not None:
        data, version = existing
        if (
            version != 1
            or data.get("config") != public
            or data.get("tls") != tls
            or data.get("cluster_id") != state["cluster_id"]
            or set(data)
            != {"config", "tls", "cluster_id", "passwords", "csrf_key", "generation", "bootstrap"}
        ):
            raise OperatorError("Installation identity or immutable configuration differs.")
    else:
        data = {
            "config": public,
            "tls": tls,
            "cluster_id": state["cluster_id"],
            "passwords": {name: secrets.token_urlsafe(32) for name in PASSWORDS},
            "csrf_key": secrets.token_hex(32),
            "generation": "self-host-" + secrets.token_hex(16),
            "bootstrap": {
                "subject": str(uuid4()),
                "user_id": str(uuid4()),
                "tenant_id": str(uuid4()),
                "membership_id": str(uuid4()),
            },
        }
        create_secret(store, token, INSTALL, data)
    ensure_mount(store, token, "signal-ephemeral", ephemeral=True)
    ensure_mount(store, token, "signal-authority")
    create_secret(
        store, token, "/signal-authority/data/recovery/current", {"generation": data["generation"]}
    )
    auth = store.request("GET", "/sys/auth", token=token)["data"]
    if "approle/" not in auth:
        store.request("POST", "/sys/auth/approle", token=token, payload={"type": "approle"})
    elif auth["approle/"].get("type") != "approle":
        raise OperatorError("Incompatible workload authentication mount.")
    for role, policy in POLICIES.items():
        name = PREFIX + role
        policy += "\n" + SELF
        found = store.request("GET", "/sys/policies/acl/" + name, token=token, expected=(200, 404))
        if getattr(found, "status", 200) == 404:
            store.request(
                "PUT",
                "/sys/policies/acl/" + name,
                token=token,
                payload={"policy": policy, "cas": 0, "cas_required": True},
            )
        elif found.get("data", found).get("policy") != policy:
            raise OperatorError("Workload policy differs; no authority enlargement attempted.")
        path = "/auth/approle/role/" + name
        found = store.request("GET", path, token=token, expected=(200, 404))
        expected = {
            "bind_secret_id": True,
            "secret_id_num_uses": 1,
            "secret_id_ttl": 1800,
            "token_policies": [name],
            "token_no_default_policy": True,
            "token_period": 300,
            "token_type": "service",
        }
        if getattr(found, "status", 200) == 404:
            store.request("POST", path, token=token, payload=expected)
        elif any(found.get("data", {}).get(key) != value for key, value in expected.items()):
            raise OperatorError("Workload role differs; no repair attempted.")
    return data


def put_provider(store, token, provider, data):
    check_audit(store, token)
    data = provider_configuration(provider, data)
    mount, path, _ = PROVIDERS[provider]
    ensure_mount(store, token, mount)
    return create_secret(store, token, f"/{mount}/data/{path}", data)


def configured_providers(store, token):
    mounts = store.request("GET", "/sys/mounts", token=token)["data"]
    result = []
    for provider, (mount, path, _) in PROVIDERS.items():
        if mount + "/" not in mounts:
            continue
        value = kv_read(store, token, f"/{mount}/data/{path}")
        if value is not None:
            provider_configuration(provider, value[0])
            result.append(provider)
    return result


def approval(store, token, data, *, arm=False, renew=False):
    found = kv_read(store, token, APPROVAL)
    if found is not None:
        document, version = found
        if document.get("status") not in {"armed", "complete"} or any(
            document.get(key) != value for key, value in data["bootstrap"].items()
        ):
            raise OperatorError("Owner bootstrap state differs.")
        if document["status"] == "complete" or not arm:
            return document
        if not renew:
            if not document["approved_at"] <= int(time.time()) < document["expires_at"]:
                raise OperatorError("Approval expired; explicitly rearm with human approval.")
            return document
    elif not arm:
        raise OperatorError("First-owner bootstrap must be explicitly human-approved.")
    else:
        version = 0
    now = int(time.time())
    document = {
        **data["bootstrap"],
        "status": "armed",
        "approved_at": now,
        "expires_at": now + 3600,
    }
    store.request(
        "POST", APPROVAL, token=token, payload={"options": {"cas": version}, "data": document}
    )
    return document


def initial_password(store, token):
    value = kv_read(store, token, INITIAL_PASSWORD)
    if value is not None:
        if value[1] != 1 or set(value[0]) != {"password"}:
            raise OperatorError("Initial password generation rejected.")
        return value[0]["password"]
    password = hidden("Initial owner password")
    if len(password) < 20 or len(password) > 1024 or any(ord(c) < 32 for c in password):
        raise OperatorError("Use a hidden owner password of at least 20 characters.")
    create_secret(store, token, INITIAL_PASSWORD, {"password": password})
    return password


class IdentityAdmin:
    def __init__(self, url, ca, password):
        from integration_secrets import local_url

        self.client = httpx2.Client(
            base_url=local_url(url) + "/identity",
            verify=ssl.create_default_context(cadata=read_private(ca).decode()),
            trust_env=False,
            follow_redirects=False,
            timeout=10,
        )
        self.tokens = self.request(
            "POST",
            "/realms/master/protocol/openid-connect/token",
            data={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": "signal-self-host-operator",
                "password": password,
            },
        )
        self.headers = {"Authorization": "Bearer " + self.tokens["access_token"]}

    def request(self, method, path, *, expected=(200, 201, 204), **kwargs):
        with self.client.stream(method, path, **kwargs) as response:
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > LIMIT:
                    raise OperatorError("Identity response exceeded its bound.")
            if response.status_code not in expected:
                raise OperatorError("Private identity operation failed; no automatic retry.")
            return json.loads(content, object_pairs_hook=unique_object) if content else {}

    def admin(self, method, path, **kwargs):
        return self.request(method, "/admin/realms/signal" + path, headers=self.headers, **kwargs)

    def close(self):
        try:
            self.request(
                "POST",
                "/realms/master/protocol/openid-connect/logout",
                expected=(204, 400, 401),
                data={"client_id": "admin-cli", "refresh_token": self.tokens["refresh_token"]},
            )
        finally:
            self.client.close()


class OperatorReply(dict):
    def __init__(self, value, status=200):
        super().__init__(value)
        self.status = status


class SelfHostStore(Store):
    """Keep HTTP absence distinct from malformed success or a failed request."""

    def request(self, method, path, *, token=None, payload=None, expected=(200, 204), timeout=5):
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["X-Vault-Token"] = token
        with self.client.stream(
            method, "/v1" + path, headers=headers, json=payload, timeout=timeout
        ) as response:
            if response.status_code not in expected:
                raise OperatorError("Secret-store request failed; values suppressed.")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > LIMIT:
                    raise OperatorError("Secret-store response exceeded its bound.")
            if not content:
                return OperatorReply({}, response.status_code)
            if response.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise OperatorError("Secret-store response was not JSON.")
            document = json.loads(content, object_pairs_hook=unique_object)
            if not isinstance(document, dict):
                raise OperatorError("Secret-store response was not an object.")
            return OperatorReply(document, response.status_code)


def retire_identity(url, ca, password):
    admin = IdentityAdmin(url, ca, password)
    try:
        users = admin.request(
            "GET",
            "/admin/realms/master/users",
            headers=admin.headers,
            params={"username": "signal-self-host-operator", "exact": "true"},
        )
        if (
            len(users) != 1
            or users[0].get("username") != "signal-self-host-operator"
            or users[0].get("enabled") is not True
        ):
            raise OperatorError("Exact bootstrap administrator unavailable for retirement.")
        admin.request(
            "PUT",
            "/admin/realms/master/users/" + users[0]["id"],
            headers=admin.headers,
            json={"enabled": False},
        )
        admin.request(
            "GET",
            "/admin/realms/master/users/" + users[0]["id"],
            headers=admin.headers,
            expected=(401,),
        )
        denied = admin.request(
            "POST",
            "/realms/master/protocol/openid-connect/token",
            expected=(400,),
            data={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": "signal-self-host-operator",
                "password": password,
            },
        )
        if denied.get("error") != "invalid_grant":
            raise OperatorError("Retired administrator credential denial not acknowledged.")
    finally:
        admin.close()
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "prepare",
            "init",
            "unseal",
            "bootstrap",
            "arm-owner",
            "complete-owner",
            "rehydrate",
            "provider",
            "backup",
            "status",
            "compose",
            "upgrade",
        ),
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime-directory", type=Path, required=True)
    parser.add_argument("--recovery-directory", type=Path, required=True)
    parser.add_argument("--bao-url", default="https://localhost:18200")
    parser.add_argument("--identity-url", default="https://localhost:18443")
    parser.add_argument("--pgp-key", type=Path, action="append", default=[])
    parser.add_argument("--root-pgp-key", type=Path)
    parser.add_argument("--public-cert", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--backup-directory", type=Path)
    parser.add_argument("--provider", choices=tuple(PROVIDERS))
    parser.add_argument("--human-approved", action="store_true")
    parser.add_argument("--restart-approved", action="store_true")
    args, compose_arguments = parser.parse_known_args(argv)
    args.compose_arguments = compose_arguments
    store = None
    try:
        config = configuration(args.config)
        if args.operation != "compose" and compose_arguments:
            raise OperatorError("Unexpected operator arguments rejected.")
        runtime = runtime_directory(args.runtime_directory)
        recovery = private_directory(external_path(args.recovery_directory))
        from self_host_material import prepare, render

        if args.operation == "prepare":
            prepare(config, runtime, recovery, hidden("TLS recovery passphrase"))
            print(
                "Private TLS prepared. Start only OpenBao; then open the private operator tunnels."
            )
            return 0
        compose = Compose(config, runtime)
        if args.operation == "compose":
            if args.compose_arguments not in [
                ["config"],
                ["up", "-d", "openbao"],
                ["ps"],
                ["stop"],
                ["down"],
            ]:
                raise OperatorError(
                    "Only reviewed Compose operations are allowed; no volume deletion."
                )
            if args.compose_arguments[:2] == ["up", "-d"]:
                compose.ensure_volumes()
            compose.call(*args.compose_arguments)
            print("Reviewed Compose operation completed; output suppressed.")
            return 0
        store = SelfHostStore(args.bao_url, recovery / "ca.pem")
        if args.operation == "init":
            initialize(
                store,
                recovery,
                [
                    base64.b64encode(read_private(external_path(path), maximum=16384)).decode()
                    for path in args.pgp_key
                ],
                base64.b64encode(
                    read_private(external_path(args.root_pgp_key), maximum=16384)
                ).decode()
                if args.root_pgp_key
                else "",
            )
            print(
                "OpenBao initialized. Distribute encrypted shares to independent custodians; "
                "unseal privately."
            )
            return 0
        if args.operation == "unseal":
            unseal(store)
            print("OpenBao unsealed; application and provider qualification are unchanged.")
            return 0
        token = hidden("Short-lived OpenBao operator token")
        if args.operation == "backup":
            if args.backup_directory is None:
                raise OperatorError("A new external protected backup directory is required.")
            check_audit(store, token)
            directory = private_directory(external_path(args.backup_directory))
            snapshot(store, directory, operator_token=token)
            print("Encrypted snapshot captured. Export its key separately and rehearse recovery.")
            return 0
        if args.operation == "provider":
            if args.provider is None:
                raise OperatorError("One explicit provider is required.")
            data = {field: hidden(field) for field in PROVIDERS[args.provider][2]}
            if args.provider == "github":
                encoded = data["private_key_pem"]
                data["private_key_pem"] = base64.b64decode(encoded, validate=True).decode()
            put_provider(store, token, args.provider, data)
            print(
                "Owner provider configuration stored; capability remains unavailable "
                "pending qualification."
            )
            return 0
        if args.operation == "status":
            for item in provider_projection(configured_providers(store, token)):
                print(
                    item["provider"]
                    + ": "
                    + item["configuration"]
                    + "; unavailable ("
                    + item["reason"]
                    + ")"
                )
            print(
                "Self-host NOT_CERTIFIED; production and model work disabled. "
                "An admitted Jev fallback must remain labelled."
            )
            return 0
        installed = kv_read(store, token, INSTALL)
        if args.operation == "bootstrap":
            if not args.human_approved:
                raise OperatorError("First-owner setup requires explicit human approval.")
            from self_host_material import tls_bundle

            data = provision(
                store,
                token,
                config,
                tls_bundle(config, recovery, hidden("TLS recovery passphrase")),
            )
        else:
            if installed is None or installed[1] != 1:
                raise OperatorError("Installation configuration is missing or differs.")
            data = installed[0]
            if any(
                getattr(config, field) != data["config"][field]
                for field in (
                    "project",
                    "origin",
                    "owner_username",
                    "workspace_name",
                    "home_region",
                )
            ):
                raise OperatorError("Installation identity differs.")
        release = kv_read(store, token, RELEASE)
        if release is None:
            create_secret(store, token, RELEASE, {"images": data["config"]["images"]})
            release = ({"images": data["config"]["images"]}, 1)
        if release[0] != {"images": config.images} and args.operation != "upgrade":
            raise OperatorError("Image selection differs; use the explicit upgrade operation.")
        if args.operation == "complete-owner":
            if not args.human_approved:
                raise OperatorError("Creating the first application owner requires human approval.")
            current = approval(store, token, data)
            retired = recovery / "owner-retired.json"
            receipt = {
                "cluster_id": data["cluster_id"],
                "tenant_id": data["bootstrap"]["tenant_id"],
            }
            if retired.exists():
                if current["status"] != "complete" or json.loads(read_private(retired)) != receipt:
                    raise OperatorError("Retirement receipt differs; reconcile before retrying.")
                print("First owner already completed; no credentials or authority changed.")
                return 0
            if current["status"] != "complete":
                proof = compose.call(
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "-c",
                    "from signal_api.integration_runtime import private_file;"
                    "from pathlib import Path;import sys;"
                    "sys.stdout.buffer.write(private_file("
                    "Path('/tmp/self-host-owner-proof.json')))",
                )
                compose.call(
                    "run", "--rm", "--no-deps", "-T", "bootstrap-job", "owner", content=proof
                )
                version = kv_read(store, token, APPROVAL)[1]
                store.request(
                    "POST",
                    APPROVAL,
                    token=token,
                    payload={
                        "options": {"cas": version},
                        "data": {
                            **current,
                            "status": "complete",
                            "proof_sha256": hashlib.sha256(proof).hexdigest(),
                            "attempt_id": json.loads(proof)["attempt_id"],
                        },
                    },
                )
            retire_identity(
                args.identity_url, recovery / "ca.pem", data["passwords"]["keycloak_bootstrap"]
            )
            compose.call("stop", "api", "dashboard")
            for path in (
                runtime / "identity/bootstrap-password",
                runtime / "api/bootstrap.json",
                runtime / "job/bootstrap.json",
                runtime / "job/postgres-password",
                runtime / "identity/realm.json",
            ):
                path.unlink(missing_ok=True)
            store.request("POST", "/auth/token/revoke-self", token=token)
            store.request("GET", "/auth/token/lookup-self", token=token, expected=(403,))
            write_private(retired, json.dumps(receipt).encode())
            print(
                "First owner committed and bootstrap credentials retired. Rehydrate "
                "with a fresh operator before restarting the API. No external grants."
            )
            return 0
        if args.operation in {"rehydrate", "upgrade", "arm-owner"}:
            if args.operation == "arm-owner" and not args.human_approved:
                raise OperatorError("Rearming the owner window requires human approval.")
            if not args.restart_approved:
                raise OperatorError(
                    "Rehydration/upgrades require explicit restart approval and a backup."
                )
            compose.call("stop", "api", "dashboard", "workflow-consumer")
        if args.operation == "upgrade":
            if any(
                getattr(config, field) != data["config"][field]
                for field in (
                    "project",
                    "origin",
                    "owner_username",
                    "workspace_name",
                    "home_region",
                )
            ):
                raise OperatorError("Upgrades cannot change installation identity.")
        armed = approval(
            store,
            token,
            data,
            arm=args.operation in {"bootstrap", "arm-owner"} and args.human_approved,
            renew=args.operation == "arm-owner",
        )
        render(
            store,
            token,
            config,
            runtime,
            data,
            armed,
            args.public_cert,
            args.public_key,
            restart=args.restart_approved,
            owner_password=initial_password(store, token),
        )
        compose.ensure_volumes()
        compose.call(
            "up",
            "-d",
            "application-database",
            "identity-database",
            "temporal-database",
            "identity",
            timeout=240,
        )
        compose.call("run", "--rm", "--no-deps", "-T", "bootstrap-job", "migrate", timeout=240)
        compose.call("run", "--rm", "--no-deps", "-T", "temporal-schema", "schema", timeout=240)
        compose.call("up", "-d", "temporal", "api", "dashboard", "ingress")
        compose.call("run", "--rm", "--no-deps", "-T", "temporal-schema", "namespace", timeout=120)
        compose.call("up", "-d", "workflow-consumer")
        if args.operation == "upgrade" and release[0] != {"images": config.images}:
            store.request(
                "POST",
                RELEASE,
                token=token,
                payload={"options": {"cas": release[1]}, "data": {"images": config.images}},
            )
        print(
            "Migrations and private services started. Open the configured dashboard, "
            "replace the initial password and enroll OTP; then run complete-owner. "
            "All provider work remains unavailable; no production certification."
        )
        return 0
    except Exception:
        print(
            "Self-host operation refused or failed; values suppressed. "
            "Preserve state and reconcile unknown outcomes before retrying.",
            file=sys.stderr,
        )
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
