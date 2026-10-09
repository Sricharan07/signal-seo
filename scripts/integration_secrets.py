"""Operator-only TLS secret-store bootstrap; never a Signal runtime credential."""

import argparse
import getpass
import json
import os
import re
import ssl
import stat
import sys
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 65536


class OperatorError(RuntimeError):
    """Only fixed, credential-free diagnostic text may leave this tool."""


def private_directory(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise OperatorError("Use an absolute, nonsymlink operator directory.")
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise OperatorError("Recovery material must be outside the repository.")
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    details = path.stat()
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o700:
        raise OperatorError("Operator directory must be owner-only mode 0700.")
    return resolved


def write_private(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_private(path: Path, maximum: int = LIMIT) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        details = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
            or details.st_size > maximum
        ):
            raise OperatorError("Operator file must be a small owner-only regular file.")
        return source.read(maximum + 1)


def prepare_tls(directory: Path) -> None:
    now = datetime.now(UTC)
    authority_key = ec.generate_private_key(ec.SECP256R1())
    authority_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Signal test CA")])
    authority = (
        x509.CertificateBuilder()
        .subject_name(authority_name)
        .issuer_name(authority_name)
        .public_key(authority_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(authority_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "openbao")]))
        .issuer_name(authority_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.DNSName("openbao"),
                    x509.IPAddress(ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .sign(authority_key, hashes.SHA256())
    )
    for name, key in (("ca-key.pem", authority_key), ("server-key.pem", server_key)):
        write_private(
            directory / name,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
    write_private(directory / "ca.pem", authority.public_bytes(serialization.Encoding.PEM))
    write_private(directory / "server.pem", server.public_bytes(serialization.Encoding.PEM))


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "localhost"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or parsed.port is None
    ):
        raise OperatorError("Use the localhost HTTPS SSH tunnel, with an explicit port.")
    return value.rstrip("/")


class Store:
    def __init__(self, base_url: str, ca: Path):
        self.client = httpx2.Client(
            base_url=local_url(base_url),
            verify=ssl.create_default_context(cadata=read_private(ca).decode("ascii")),
            timeout=5,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method, path, *, token=None, payload=None, expected=(200, 204), timeout=5):
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["X-Vault-Token"] = token
        with self.client.stream(
            method, f"/v1{path}", headers=headers, json=payload, timeout=timeout
        ) as reply:
            if reply.status_code not in expected:
                raise OperatorError(f"Secret-store request failed with HTTP {reply.status_code}.")
            content = bytearray()
            for chunk in reply.iter_bytes():
                content.extend(chunk)
                if len(content) > LIMIT:
                    raise OperatorError("Secret-store response exceeded the size bound.")
            if not content:
                return {}
            if reply.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise OperatorError("Secret-store response was not JSON.")
            document = json.loads(content)
            if not isinstance(document, dict):
                raise OperatorError("Secret-store response was not an object.")
            return document


def recovery(directory: Path) -> dict:
    result = json.loads(read_private(directory / "recovery.json"))
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("root_token"), str)
        or not isinstance(result.get("keys_base64"), list)
        or len(result["keys_base64"]) != 3
        or any(not isinstance(key, str) for key in result["keys_base64"])
    ):
        raise OperatorError("Recovery material has an unexpected format.")
    return result


def initialize(store: Store, directory: Path) -> None:
    if (directory / "recovery.json").exists():
        raise OperatorError("Recovery file already exists; refusing initialization.")
    state = store.request("GET", "/sys/init")
    if state.get("initialized") is not False:
        raise OperatorError("Store is already initialized or its state is unknown.")
    bundle = store.request(
        "PUT", "/sys/init", payload={"secret_shares": 3, "secret_threshold": 2}, timeout=90
    )
    # Persist the independently held recovery material before using any share.
    write_private(directory / "recovery.json", json.dumps(bundle).encode("utf-8"))
    unseal(store, directory)
    check_audit(store, recovery(directory)["root_token"])


def check_audit(store: Store, root: str) -> None:
    response = store.request("GET", "/sys/audit", token=root)
    record = response.get("data", response).get("operator-file/", {})
    options = record.get("options", {})
    if (
        record.get("type") != "file"
        or options.get("file_path") != "/openbao/logs/audit.jsonl"
        or options.get("log_raw") != "false"
        or options.get("mode") != "0600"
    ):
        raise OperatorError("Required HMAC-redacted audit configuration is missing.")


def unseal(store: Store, directory: Path) -> None:
    bundle = recovery(directory)
    for key in bundle["keys_base64"][:2]:
        store.request("PUT", "/sys/unseal", payload={"key": key}, timeout=90)
    state = store.request("GET", "/sys/seal-status")
    if state.get("sealed") is not False or state.get("t") != 2 or state.get("n") != 3:
        raise OperatorError("Unseal threshold or final state did not match.")


def qualify(store: Store, directory: Path) -> None:
    root = recovery(directory)["root_token"]
    check_audit(store, root)
    mount = "signal-qualification"
    store.request(
        "POST",
        f"/sys/mounts/{mount}",
        token=root,
        payload={"type": "kv", "options": {"version": "2"}},
    )
    reader = None
    try:
        store.request(
            "POST",
            f"/{mount}/data/canary",
            token=root,
            payload={"data": {"value": "synthetic-persistent-canary"}},
        )
        policy = f'path "{mount}/data/canary" {{ capabilities = ["read"] }}'
        store.request(
            "PUT",
            "/sys/policies/acl/signal-qualification-reader",
            token=root,
            payload={"policy": policy},
        )
        issued = store.request(
            "POST",
            "/auth/token/create",
            token=root,
            payload={
                "policies": ["signal-qualification-reader"],
                "no_default_policy": True,
                "ttl": "10m",
            },
        )
        reader = issued["auth"]["client_token"]
        assert_canary(store, reader)
        for method, path, payload in (
            ("POST", f"/{mount}/data/canary", {"data": {"value": "synthetic-denied"}}),
            ("DELETE", f"/{mount}/metadata/canary", None),
            ("GET", f"/{mount}/data/other", None),
            ("POST", "/auth/token/create", {}),
        ):
            store.request(method, path, token=reader, payload=payload, expected=(403,))
        store.request("GET", f"/{mount}/data/canary", expected=(403,))
        store.request("PUT", "/sys/seal", token=root)
        store.request("GET", "/sys/health", expected=(503,))
        store.request("GET", f"/{mount}/data/canary", token=reader, expected=(503,))
        unseal(store, directory)
        assert_canary(store, reader)
        store.request("POST", "/auth/token/revoke", token=root, payload={"token": reader})
        store.request("GET", f"/{mount}/data/canary", token=reader, expected=(403,))
        reader = None
    finally:
        if reader is not None:
            store.request("POST", "/auth/token/revoke", token=root, payload={"token": reader})
        store.request("DELETE", f"/sys/mounts/{mount}", token=root)
        store.request("DELETE", "/sys/policies/acl/signal-qualification-reader", token=root)


def assert_canary(store: Store, token: str) -> None:
    value = store.request("GET", "/signal-qualification/data/canary", token=token)
    if value.get("data", {}).get("data") != {"value": "synthetic-persistent-canary"}:
        raise OperatorError("Persistent canary did not match.")


def snapshot(store: Store, directory: Path, *, operator_token: str | None = None) -> None:
    if any((directory / name).exists() for name in ("snapshot-key.bin", "snapshot.enc")):
        raise OperatorError("Snapshot files already exist; refusing to replace recovery material.")
    token = operator_token if operator_token is not None else recovery(directory)["root_token"]
    content = bytearray()
    with store.client.stream(
        "GET",
        "/v1/sys/storage/raft/snapshot",
        headers={"X-Vault-Token": token},
    ) as reply:
        if reply.status_code != 200:
            raise OperatorError("Snapshot request failed.")
        for chunk in reply.iter_raw():
            content.extend(chunk)
            if len(content) > 64 * 1024**2:
                raise OperatorError("Test snapshot exceeded the 64 MiB bound.")
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    encrypted = nonce + AESGCM(key).encrypt(nonce, bytes(content), b"signal-test-snapshot-v1")
    write_private(directory / "snapshot-key.bin", key)
    write_private(directory / "snapshot.enc", encrypted)


def put_model_keys(store: Store, directory: Path, keys: dict) -> None:
    if (
        not isinstance(keys, dict)
        or set(keys) != {"openai", "jev"}
        or not isinstance(keys["openai"], str)
        or re.fullmatch(r"sk-[A-Za-z0-9_-]{16,496}", keys["openai"]) is None
        or not isinstance(keys["jev"], str)
        or re.fullmatch(r"[!-~]{16,512}", keys["jev"]) is None
    ):
        raise OperatorError("Model credential formats were rejected; no values written.")
    root = recovery(directory)["root_token"]
    check_audit(store, root)
    mounts = store.request("GET", "/sys/mounts", token=root)
    mounts = mounts.get("data", mounts)
    for provider, mount, path, policy in (
        ("openai", "signal-model", "openai/default", "signal-model-reader"),
        ("jev", "signal-decision", "typesafe/default", "signal-decision-reader"),
    ):
        current = mounts.get(f"{mount}/")
        if current is None:
            store.request(
                "POST",
                f"/sys/mounts/{mount}",
                token=root,
                payload={"type": "kv", "options": {"version": "2"}},
            )
        elif current.get("type") != "kv" or current.get("options", {}).get("version") != "2":
            raise OperatorError("Existing secret mount is incompatible.")
        store.request(
            "POST",
            f"/{mount}/config",
            token=root,
            payload={"cas_required": True, "max_versions": 1},
        )
        store.request(
            "POST",
            f"/{mount}/data/{path}",
            token=root,
            payload={"options": {"cas": 0}, "data": {"api_key": keys[provider]}},
        )
        store.request(
            "PUT",
            f"/sys/policies/acl/{policy}",
            token=root,
            payload={"policy": f'path "{mount}/data/{path}" {{ capabilities = ["read"] }}'},
        )
        verify_model_reader(store, root, mount, path, policy, keys[provider])


def verify_model_reader(
    store: Store, root: str, mount: str, path: str, policy: str, key: str
) -> None:
    issued = store.request(
        "POST",
        "/auth/token/create",
        token=root,
        payload={"policies": [policy], "no_default_policy": True, "ttl": "5m"},
    )
    reader = issued["auth"]["client_token"]
    try:
        record = store.request("GET", f"/{mount}/data/{path}", token=reader)
        data = record.get("data", {})
        if data.get("data") != {"api_key": key} or data.get("metadata", {}).get("version") != 1:
            raise OperatorError("Stored model credential or generation did not match.")
        store.request(
            "POST",
            f"/{mount}/data/{path}",
            token=reader,
            payload={"data": {"api_key": "synthetic-denied-key"}},
            expected=(403,),
        )
        store.request("GET", f"/{mount}/data/other", token=reader, expected=(403,))
        store.request("DELETE", f"/{mount}/metadata/{path}", token=reader, expected=(403,))
    finally:
        store.request("POST", "/auth/token/revoke", token=root, payload={"token": reader})


def private_key_input(use_stdin: bool) -> dict:
    if use_stdin:
        content = sys.stdin.buffer.read(LIMIT + 1)
        if len(content) > LIMIT:
            raise OperatorError("Credential input exceeded the size bound.")
        return json.loads(content)
    if not sys.stdin.isatty():
        raise OperatorError("Use the private terminal prompts, or explicit bounded stdin.")
    return {
        "openai": getpass.getpass("OpenAI key (hidden): "),
        "jev": getpass.getpass("Jev key (hidden): "),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "prepare-tls",
            "init",
            "unseal",
            "status",
            "qualify",
            "snapshot",
            "put-model-keys",
        ),
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--url", default="https://localhost:18200")
    parser.add_argument(
        "--stdin", action="store_true", help="Explicit non-echoed JSON credential input"
    )
    args = parser.parse_args()
    store = None
    try:
        directory = private_directory(args.directory)
        if args.operation == "prepare-tls":
            prepare_tls(directory)
        else:
            store = Store(args.url, directory / "ca.pem")
            if args.operation == "init":
                initialize(store, directory)
            elif args.operation == "unseal":
                unseal(store, directory)
            elif args.operation == "qualify":
                qualify(store, directory)
            elif args.operation == "snapshot":
                snapshot(store, directory)
            elif args.operation == "put-model-keys":
                put_model_keys(store, directory, private_key_input(args.stdin))
            else:
                state = store.request("GET", "/sys/seal-status")
                print(
                    json.dumps(
                        {
                            key: state.get(key)
                            for key in ("initialized", "sealed", "t", "n", "version")
                        }
                    )
                )
        print(f"Secret-store {args.operation}: PASS; no credential values displayed.")
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, OperatorError) else type(error).__name__
        print(
            f"Secret-store operation failed ({reason}); credential values suppressed.",
            file=sys.stderr,
        )
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
