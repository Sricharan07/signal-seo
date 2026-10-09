"""Prepare private operator materials for the dedicated persistent identity stack."""

import argparse
import re
import secrets
import sys
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from integration_connector_secrets import verify_reader
from integration_environment import load_integration_scope
from integration_secrets import (
    OperatorError,
    Store,
    check_audit,
    private_directory,
    read_private,
    recovery,
    write_private,
)

MOUNT = "signal-identity"
PATH = "platform/callback-stack"
POLICY = "signal-identity-infrastructure-reader"
FIELDS = {"postgres_password", "keycloak_db_password", "keycloak_bootstrap_password"}
FILES = (
    "postgres-password",
    "keycloak-password",
    "bootstrap-password",
    "signal_google",
    "ca.pem",
    "database.pem",
    "database-key.pem",
    "identity.pem",
    "identity-key.pem",
)


def validate_platform(data: object) -> dict:
    if (
        not isinstance(data, dict)
        or set(data) != FIELDS
        or any(
            not isinstance(v, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", v) is None
            for v in data.values()
        )
        or len(set(data.values())) != len(FIELDS)
    ):
        raise OperatorError("Dedicated infrastructure configuration was rejected.")
    return data


def read_configuration(store: Store, root: str, path: str) -> dict:
    envelope = store.request("GET", f"/{MOUNT}/data/{path}", token=root).get("data", {})
    if (
        type(envelope.get("metadata", {}).get("version")) is not int
        or envelope.get("metadata", {}).get("version") != 1
        or not isinstance(envelope.get("data"), dict)
    ):
        raise OperatorError("Identity configuration generation is not the qualified generation.")
    return envelope["data"]


def create_platform(store: Store, root: str) -> dict:
    check_audit(store, root)
    config = store.request("GET", f"/{MOUNT}/config", token=root).get("data", {})
    policies = store.request("LIST", "/sys/policies/acl", token=root).get("data", {}).get("keys")
    if (
        config.get("cas_required") is not True
        or not isinstance(policies, list)
        or POLICY in policies
    ):
        raise OperatorError("Create-only identity prerequisite failed; no replacement attempted.")
    data = validate_platform({field: secrets.token_urlsafe(32) for field in sorted(FIELDS)})
    store.request(
        "POST", f"/{MOUNT}/data/{PATH}", token=root, payload={"options": {"cas": 0}, "data": data}
    )
    store.request(
        "PUT",
        f"/sys/policies/acl/{POLICY}",
        token=root,
        payload={
            "policy": f'path "{MOUNT}/data/{PATH}" {{ capabilities = ["read"] }}',
            "cas": 0,
            "cas_required": True,
        },
    )
    verify_reader(store, root, MOUNT, PATH, POLICY, data)
    return data


def leaf_certificates(
    directory: Path,
    authority_directory: Path,
    names: tuple[str, ...] = ("database", "identity"),
) -> None:
    authority = x509.load_pem_x509_certificate(read_private(authority_directory / "ca.pem"))
    key = serialization.load_pem_private_key(
        read_private(authority_directory / "ca-key.pem"), password=None
    )
    now = datetime.now(UTC)
    if (
        not isinstance(key, ec.EllipticCurvePrivateKey)
        or authority.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        != key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        or not authority.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        or not authority.not_valid_before_utc
        <= now
        < authority.not_valid_after_utc - timedelta(days=1)
    ):
        raise OperatorError("Private identity certificate authority is invalid or expiring.")
    write_private(directory / "ca.pem", authority.public_bytes(serialization.Encoding.PEM))
    for name in names:
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(authority.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(min(now + timedelta(days=30), authority.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName(name),
                        x509.DNSName("localhost"),
                        x509.IPAddress(ip_address("127.0.0.1")),
                    ]
                ),
                False,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .sign(key, hashes.SHA256())
        )
        write_private(
            directory / f"{name}.pem", certificate.public_bytes(serialization.Encoding.PEM)
        )
        write_private(
            directory / f"{name}-key.pem",
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )


def prepare(store: Store, authority_directory: Path, destination: Path, *, create: bool) -> None:
    if destination == authority_directory or authority_directory in destination.parents:
        raise OperatorError("Use a separate private identity-material directory.")
    if any((destination / filename).exists() for filename in FILES):
        raise OperatorError("Identity material already exists; no file replacement attempted.")
    root = recovery(authority_directory)["root_token"]
    check_audit(store, root)
    google = read_configuration(store, root, "google/client")
    if (
        set(google) != {"client_id", "client_secret"}
        or google["client_id"] != load_integration_scope().google_login_client_id
        or not isinstance(google["client_secret"], str)
        or re.fullmatch(r"GOCSPX-[A-Za-z0-9_-]{16,128}", google["client_secret"]) is None
    ):
        raise OperatorError("Dedicated Google sign-in configuration was rejected.")
    platform = (
        create_platform(store, root)
        if create
        else validate_platform(read_configuration(store, root, PATH))
    )
    leaf_certificates(destination, authority_directory)
    write_private(destination / "postgres-password", platform["postgres_password"].encode())
    write_private(destination / "keycloak-password", platform["keycloak_db_password"].encode())
    write_private(
        destination / "bootstrap-password", platform["keycloak_bootstrap_password"].encode()
    )
    write_private(destination / "signal_google", google["client_secret"].encode())
    import json
    from dataclasses import asdict

    from integration_environment import deployment_variables, render_realm

    scope = load_integration_scope()
    write_private(destination / "environment.json", json.dumps(asdict(scope)).encode())
    write_private(destination / "realm.json", json.dumps(render_realm(scope)).encode())
    variables = deployment_variables(scope)
    write_private(
        destination / "environment.env",
        ("\n".join(f"{key}='{value}'" for key, value in variables.items()) + "\n").encode(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "render"))
    parser.add_argument("--authority-directory", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--url", default="https://localhost:18200")
    args = parser.parse_args()
    store = None
    try:
        authority = private_directory(args.authority_directory)
        directory = private_directory(args.directory)
        store = Store(args.url, authority / "ca.pem")
        prepare(store, authority, directory, create=args.operation == "prepare")
        print("Private identity material: PASS; values suppressed; public runtime not enabled.")
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, OperatorError) else type(error).__name__
        print(
            f"Identity preparation failed ({reason}); values suppressed; no unknown-write retry.",
            file=sys.stderr,
        )
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
