"""Qualify private app imports on a fresh local Raft store using synthetic data only."""

import json
import secrets
import shutil
import tempfile
import time
from pathlib import Path

import httpx2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from integration_connector_secrets import PATHS, github_configuration, store_configuration
from integration_secrets import ROOT, OperatorError, Store, initialize, prepare_tls, recovery
from openbao_lab import IMAGE, LabError, docker

LABEL = "io.signal.connector-secret-qualification"


class QualificationStore(Store):
    def request(self, method, path, **kwargs):
        try:
            return super().request(method, path, **kwargs)
        except OperatorError as error:
            raise OperatorError(f"{method} {path}: {error}") from None


def synthetic_configurations() -> dict:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "github": github_configuration(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ),
        "google-login": {
            "client_id": "synthetic-signin-test.apps.googleusercontent.com",
            "client_secret": "synthetic-signin-secret-not-for-output",
        },
        "gsc": {
            "client_id": "synthetic-gsc-test.apps.googleusercontent.com",
            "client_secret": "synthetic-gsc-secret-not-for-output",
        },
        "slack": {
            "client_id": "00000000000.00000000000000",
            "client_secret": "synthetic-slack-secret-not-for-output",
            "signing_secret": "synthetic-signing-secret-not-for-output",
        },
    }


def qualify(store: Store, directory: Path) -> None:
    initialize(store, directory)
    configurations = synthetic_configurations()
    for provider, data in configurations.items():
        store_configuration(store, directory, provider, data)
        try:
            store_configuration(store, directory, provider, data)
        except OperatorError as error:
            if not str(error).startswith("Reader policy already exists"):
                raise
        else:
            raise OperatorError("Repeated import was not rejected.")
    root = recovery(directory)["root_token"]
    for provider, data in configurations.items():
        mount, path, policy = PATHS[provider]
        # Direct CAS-zero qualification is separate from the helper's policy guard.
        store.request(
            "POST",
            f"/{mount}/data/{path}",
            token=root,
            payload={"options": {"cas": 0}, "data": {"synthetic": "replacement"}},
            expected=(400,),
        )
        envelope = store.request("GET", f"/{mount}/data/{path}", token=root)["data"]
        if envelope.get("data") != data or envelope.get("metadata", {}).get("version") != 1:
            raise OperatorError("Rejected replacement changed stored configuration.")
        store.request(
            "PUT",
            f"/sys/policies/acl/{policy}",
            token=root,
            payload={"policy": 'path "*" { capabilities = ["read"] }', "cas": 0},
            expected=(400,),
        )
        document = store.request("GET", f"/sys/policies/acl/{policy}", token=root)
        current = document.get("data", document)
        if (
            current.get("policy") != f'path "{mount}/data/{path}" {{ capabilities = ["read"] }}'
            or current.get("cas_required") is not True
        ):
            raise OperatorError("Rejected policy replacement changed reader permissions.")
    store.request("POST", "/sys/seal", token=root)
    mount, path, _ = PATHS["gsc"]
    store.request("GET", f"/{mount}/data/{path}", token=root, expected=(503,))


def main() -> int:
    name = f"signal-connector-secrets-tests-{secrets.token_hex(6)}"
    store = None
    try:
        docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
        with tempfile.TemporaryDirectory(prefix="signal-connector-secrets-") as temporary:
            directory = Path(temporary).resolve()
            prepare_tls(directory)
            tls = directory / "container-tls"
            tls.mkdir(mode=0o755)
            for filename in ("ca.pem", "server.pem", "server-key.pem"):
                target = tls / filename
                shutil.copyfile(directory / filename, target)
                target.chmod(0o444)
            # Readable container copies remain inside the host's private 0700 parent.
            docker(
                "container",
                "create",
                "--name",
                name,
                "--label",
                f"{LABEL}={name}",
                "--publish",
                "127.0.0.1::8200",
                "--user",
                "100:1000",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "384m",
                "--memory-swap",
                "384m",
                "--cpus",
                "1",
                "--pids-limit",
                "128",
                "--ulimit",
                "core=0",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=72m,mode=1777",
                "--mount",
                f"type=bind,src={ROOT / 'deploy/integration-test/secrets/server.hcl'},"
                "dst=/openbao/config/server.hcl,readonly",
                "--mount",
                f"type=bind,src={tls},dst=/openbao/tls,readonly",
                "--volume",
                "/openbao/file",
                "--volume",
                "/openbao/logs",
                "--entrypoint",
                "bao",
                IMAGE,
                "server",
                "-config=/openbao/config/server.hcl",
            )
            docker("container", "start", name)
            description = json.loads(docker("inspect", name))[0]
            bindings = description["NetworkSettings"]["Ports"].get("8200/tcp")
            if not bindings or len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
                raise OperatorError("Qualification must expose only one loopback port.")
            store = QualificationStore(
                f"https://localhost:{bindings[0]['HostPort']}", directory / "ca.pem"
            )
            deadline = time.monotonic() + 60
            while True:
                try:
                    store.request("GET", "/sys/init")
                    break
                except (OperatorError, httpx2.HTTPError):
                    if time.monotonic() >= deadline:
                        raise OperatorError(
                            "Disposable qualification store did not start."
                        ) from None
                    time.sleep(0.2)
            qualify(store, directory)
            print("Private connector import: PASS; synthetic data, real TLS/Raft/ACL/CAS/seal.")
        return 0
    except Exception as error:
        reason = (
            str(error) if isinstance(error, (OperatorError, LabError)) else type(error).__name__
        )
        print(f"Private connector qualification failed ({reason}); values suppressed.")
        return 1
    finally:
        if store is not None:
            store.close()
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
            labels = json.loads(docker("inspect", name))[0]["Config"]["Labels"]
            if labels.get(LABEL) != name:
                raise OperatorError("Qualification cleanup identity mismatch.")
            docker("container", "rm", "--force", "--volumes", name)


if __name__ == "__main__":
    raise SystemExit(main())
