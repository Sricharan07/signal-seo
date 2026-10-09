"""TLS/bootstrap rendering into checked nonrepository tmpfs, never durable plaintext."""

import base64
import json
import os
import secrets
import stat
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from integration_application import POLICIES
from integration_secrets import OperatorError, read_private, write_private
from psycopg import sql
from psycopg.conninfo import make_conninfo
from self_host import PREFIX, ROOT, configured_providers
from signal_core.self_host_config import CALLBACKS, CLIENT

UIDS = {
    "openbao": 100,
    "application-database": 70,
    "identity-database": 70,
    "temporal-database": 70,
    "identity": 1000,
    "api": 10001,
    "dashboard": 1000,
    "temporal": 1000,
    "worker": 10001,
    "job": 10001,
    "ingress": 1000,
}


def tls_material():
    now = datetime.now(UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Signal self-host CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), True
        )
        .sign(key, hashes.SHA256())
    )
    result = {
        "ca-key.pem": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        "ca.pem": ca.public_bytes(serialization.Encoding.PEM).decode(),
        "leaves": {},
    }
    for service in UIDS:
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, service)]))
            .issuer_name(ca.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName(service),
                        x509.DNSName("localhost"),
                        x509.IPAddress(ip_address("127.0.0.1")),
                    ]
                ),
                False,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
                ),
                False,
            )
            .sign(key, hashes.SHA256())
        )
        result["leaves"][service] = {
            "server.pem": certificate.public_bytes(serialization.Encoding.PEM).decode(),
            "server-key.pem": leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
        }
    return result


def archive_key(passphrase, salt):
    if not isinstance(passphrase, str) or len(passphrase) < 20 or len(passphrase) > 1024:
        raise OperatorError(
            "Use an independently held TLS recovery passphrase of at least 20 characters."
        )
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def archive_context(config):
    return ("signal-self-host-tls-v1:" + config.project + ":" + config.origin).encode()


def tls_bundle(config, directory, passphrase):
    envelope = json.loads(read_private(directory / "tls-bootstrap.enc", maximum=131072))
    if set(envelope) != {"version", "salt", "nonce", "ciphertext"} or envelope["version"] != 1:
        raise OperatorError("TLS recovery archive rejected.")
    salt = base64.b64decode(envelope["salt"], validate=True)
    nonce = base64.b64decode(envelope["nonce"], validate=True)
    if len(salt) != 16 or len(nonce) != 12:
        raise OperatorError("TLS recovery archive rejected.")
    bundle = json.loads(
        AESGCM(archive_key(passphrase, salt)).decrypt(
            nonce, base64.b64decode(envelope["ciphertext"], validate=True), archive_context(config)
        )
    )
    return validate_tls(bundle)


def validate_tls(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {"ca.pem", "ca-key.pem", "leaves"}:
        raise OperatorError("Private TLS bundle schema rejected.")
    ca = x509.load_pem_x509_certificate(bundle["ca.pem"].encode())
    key = serialization.load_pem_private_key(bundle["ca-key.pem"].encode(), password=None)
    if ca.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) or set(bundle["leaves"]) != set(UIDS):
        raise OperatorError("TLS recovery identity differs.")
    ca.verify_directly_issued_by(ca)
    if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise OperatorError("Private trust anchor rejected.")
    current = datetime.now(UTC)
    if not ca.not_valid_before_utc <= current < ca.not_valid_after_utc - timedelta(days=1):
        raise OperatorError("Private trust anchor invalid or expiring.")
    for service, material in bundle["leaves"].items():
        leaf = x509.load_pem_x509_certificate(material["server.pem"].encode())
        leaf_key = serialization.load_pem_private_key(material["server-key.pem"].encode(), None)
        leaf.verify_directly_issued_by(ca)
        if (
            not leaf.not_valid_before_utc <= current < leaf.not_valid_after_utc - timedelta(days=1)
            or service
            not in leaf.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.DNSName)
            or leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or set(leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)
            != {ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH}
            or leaf.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            != leaf_key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ):
            raise OperatorError(
                "Private TLS leaves are invalid or expiring; stop for deliberate rotation."
            )
    return bundle


def write_runtime(path, content, uid, *, replace=False):
    content = content.encode() if isinstance(content, str) else content
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise OperatorError("Runtime symlinks are forbidden.")
    os.chown(path.parent, uid, uid if uid != 100 else 1000)
    os.chmod(path.parent, 0o700)
    if path.exists():
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != uid
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > 131072
            ):
                raise OperatorError("Existing runtime file is unsafe; no replacement attempted.")
            if stream.read(131073) == content:
                return
        if not replace:
            raise OperatorError(
                "Runtime material differs; stop its services before explicit rehydration."
            )
        path.unlink()
    write_private(path, content)
    os.chown(path, uid, uid if uid != 100 else 1000)


def prepare(config, runtime, directory, passphrase):
    archive = directory / "tls-bootstrap.enc"
    if not archive.exists():
        bundle = tls_material()
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
        ciphertext = AESGCM(archive_key(passphrase, salt)).encrypt(
            nonce, json.dumps(bundle).encode(), archive_context(config)
        )
        write_private(
            archive,
            json.dumps(
                {
                    "version": 1,
                    "salt": base64.b64encode(salt).decode(),
                    "nonce": base64.b64encode(nonce).decode(),
                    "ciphertext": base64.b64encode(ciphertext).decode(),
                }
            ).encode(),
        )
    bundle = tls_bundle(config, directory, passphrase)
    public = directory / "ca.pem"
    if not public.exists():
        write_private(public, bundle["ca.pem"].encode())
    elif read_private(public).decode() != bundle["ca.pem"]:
        raise OperatorError("Recovery certificate authority differs.")
    for name, value in {**bundle["leaves"]["openbao"], "ca.pem": bundle["ca.pem"]}.items():
        write_runtime(runtime / "openbao" / name, value, 100)


def realm(config, bootstrap=None, password=None):
    document = {
        "realm": "signal",
        "enabled": True,
        "displayName": "Signal",
        "sslRequired": "all",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "rememberMe": False,
        "loginWithEmailAllowed": False,
        "duplicateEmailsAllowed": False,
        "editUsernameAllowed": False,
        "bruteForceProtected": True,
        "failureFactor": 5,
        "defaultSignatureAlgorithm": "RS256",
        "accessTokenLifespan": 300,
        "ssoSessionIdleTimeout": 1800,
        "ssoSessionMaxLifespan": 28800,
        "eventsEnabled": True,
        "adminEventsEnabled": True,
        "adminEventsDetailsEnabled": False,
        "browserFlow": "signal-owner-login",
        "authenticationFlows": [
            {
                "alias": "signal-owner-login",
                "providerId": "basic-flow",
                "topLevel": True,
                "builtIn": False,
                "authenticationExecutions": [
                    {
                        "authenticator": "auth-username-password-form",
                        "requirement": "REQUIRED",
                        "priority": 10,
                        "userSetupAllowed": False,
                    },
                    {
                        "authenticator": "auth-otp-form",
                        "authenticatorConfig": "signal-completed-otp",
                        "requirement": "REQUIRED",
                        "priority": 20,
                        "userSetupAllowed": False,
                    },
                ],
            }
        ],
        "authenticatorConfig": [
            {
                "alias": "signal-completed-otp",
                "config": {"default.reference.value": "otp", "default.reference.maxAge": "300"},
            }
        ],
        "clients": [
            {
                "clientId": CLIENT,
                "enabled": True,
                "publicClient": True,
                "protocol": "openid-connect",
                "standardFlowEnabled": True,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": False,
                "redirectUris": [config.origin + "/auth/callback"],
                "webOrigins": [],
                "attributes": {"pkce.code.challenge.method": "S256"},
                "defaultClientScopes": ["basic", "acr", "profile", "email"],
                "protocolMappers": [
                    {
                        "name": "signal-completed-authentication-methods",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-amr-mapper",
                        "consentRequired": False,
                        "config": {"id.token.claim": "true", "access.token.claim": "false"},
                    }
                ],
            }
        ],
    }

    if bootstrap is not None:
        document["users"] = [
            {
                "id": bootstrap["subject"],
                "username": config.owner_username,
                "enabled": True,
                "emailVerified": False,
                "requiredActions": ["UPDATE_PASSWORD", "CONFIGURE_TOTP"],
                "credentials": [{"type": "password", "value": password, "temporary": True}],
            }
        ]
    return document


def ingress(config, configured):
    callbacks = [path for provider in configured for path in CALLBACKS.get(provider, ())]
    # Configuration is not admission. Exact registered callbacks fail visibly, never exchange keys.
    stanza = ""
    if callbacks:
        stanza = (
            "@configured_callbacks path "
            + " ".join(callbacks)
            + "\n  handle @configured_callbacks {\n"
            '    respond "Provider runtime unavailable" 503\n  }'
        )
    return (
        (ROOT / "deploy/self-host/Caddyfile")
        .read_text()
        .replace("__ORIGIN__", config.origin)
        .replace("__PROVIDER_CALLBACKS__", stanza)
    )


def temporal_configuration(password):
    directory = "/run/signal-temporal"
    tls = {
        "server": {
            "certFile": directory + "/tls/server.pem",
            "keyFile": directory + "/tls/server-key.pem",
            "requireClientAuth": True,
            "clientCaFiles": [directory + "/tls/ca.pem"],
        },
        "client": {"serverName": "temporal", "rootCaFiles": [directory + "/tls/ca.pem"]},
    }

    def database(name):
        return {
            "sql": {
                "pluginName": "postgres12",
                "databaseName": name,
                "connectAddr": "temporal-database:5432",
                "connectProtocol": "tcp",
                "user": "signal_temporal",
                "password": password,
                "maxConns": 10,
                "maxIdleConns": 2,
                "tls": {
                    "enabled": True,
                    "caFile": directory + "/tls/ca.pem",
                    "serverName": "temporal-database",
                    "enableHostVerification": True,
                },
            }
        }

    return {
        "log": {"level": "error", "stdout": True},
        "persistence": {
            "defaultStore": "default",
            "visibilityStore": "visibility",
            "numHistoryShards": 4,
            "datastores": {
                "default": database("temporal"),
                "visibility": database("temporal_visibility"),
            },
        },
        "global": {
            "membership": {"maxJoinDuration": "30s", "broadcastAddress": "127.0.0.1"},
            "metrics": {"prometheus": {"listenAddress": "127.0.0.1:8000"}},
            "tls": {"internode": tls, "frontend": tls},
        },
        "services": {
            name: {
                "rpc": {
                    "grpcPort": port,
                    "membershipPort": membership,
                    "bindOnLocalHost": name != "frontend",
                }
            }
            for name, port, membership in (
                ("frontend", 7233, 6933),
                ("history", 7234, 6934),
                ("matching", 7235, 6935),
                ("worker", 7239, 6939),
            )
        },
        "clusterMetadata": {
            "enableGlobalNamespace": False,
            "failoverVersionIncrement": 10,
            "masterClusterName": "self-host",
            "currentClusterName": "self-host",
            "clusterInformation": {
                "self-host": {
                    "enabled": True,
                    "initialFailoverVersion": 1,
                    "rpcAddress": "temporal:7233",
                }
            },
        },
        "publicClient": {"hostPort": "temporal:7233"},
    }


def render(
    store,
    token,
    config,
    runtime,
    data,
    bootstrap,
    public_cert,
    public_key,
    *,
    restart=False,
    owner_password=None,
):
    from self_host import external_path

    if public_cert is None or public_key is None:
        raise OperatorError("Explicit externally obtained public TLS certificate/key required.")
    cert = read_private(external_path(public_cert))
    key_bytes = read_private(external_path(public_key))
    leaf = x509.load_pem_x509_certificate(cert)
    key = serialization.load_pem_private_key(key_bytes, password=None)
    if (
        urlsplit(config.origin).hostname
        not in leaf.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
        or not leaf.not_valid_before_utc
        <= datetime.now(UTC)
        < leaf.not_valid_after_utc - timedelta(days=1)
        or leaf.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        != key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ):
        raise OperatorError("Public TLS key, exact hostname or expiry rejected.")
    tls = validate_tls(data["tls"])
    passwords = data["passwords"]
    configured = configured_providers(store, token)

    def put(service, filename, value):
        write_runtime(runtime / service / filename, value, UIDS[service], replace=restart)

    def document(service, filename, value):
        put(service, filename, json.dumps(value, separators=(",", ":")))

    for service in UIDS:
        for name, value in {**tls["leaves"][service], "ca.pem": tls["ca.pem"]}.items():
            put(service, name if service in {"openbao", "ingress"} else "tls/" + name, value)
    for service, field in (
        ("application-database", "postgres"),
        ("identity-database", "identity_postgres"),
        ("temporal-database", "temporal_postgres"),
    ):
        put(service, "postgres-password", passwords[field])
        put(service, "pg_hba.conf", (ROOT / "deploy/self-host/pg_hba.conf").read_text())

    def password_sql(role, field):
        return (
            sql.SQL("ALTER ROLE {} LOGIN PASSWORD {};\n")
            .format(sql.Identifier(role), sql.Literal(passwords[field]))
            .as_string()
        )

    statement = (
        ROOT / "database/bootstrap.sql"
    ).read_text() + "\nREVOKE ALL ON DATABASE signal FROM PUBLIC;\n"
    for role in ("signal_migrator", "signal_identity", "signal_scheduler", "signal_workflow"):
        statement += (
            password_sql(role, role)
            + sql.SQL("GRANT CONNECT ON DATABASE signal TO {};\n")
            .format(sql.Identifier(role))
            .as_string()
        )
    put("application-database", "initialize.sql", statement)
    put(
        "identity-database",
        "initialize.sql",
        "REVOKE ALL ON DATABASE keycloak FROM PUBLIC;\n"
        "CREATE ROLE signal_keycloak LOGIN NOSUPERUSER NOCREATEDB "
        "NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n"
        + password_sql("signal_keycloak", "keycloak")
        + "GRANT CONNECT ON DATABASE keycloak TO signal_keycloak;\n"
        "ALTER SCHEMA public OWNER TO signal_keycloak;\n"
        "REVOKE ALL ON SCHEMA public FROM PUBLIC;\n",
    )
    put(
        "temporal-database",
        "initialize.sql",
        "CREATE ROLE signal_temporal LOGIN NOSUPERUSER NOCREATEDB "
        "NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n"
        + password_sql("signal_temporal", "temporal")
        + "ALTER DATABASE temporal OWNER TO signal_temporal;\n"
        "REVOKE ALL ON DATABASE temporal FROM PUBLIC;\n"
        "CREATE DATABASE temporal_visibility OWNER signal_temporal;\n"
        "REVOKE ALL ON DATABASE temporal_visibility FROM PUBLIC;\n"
        "REVOKE ALL ON SCHEMA public FROM PUBLIC;\n"
        "GRANT USAGE,CREATE ON SCHEMA public TO signal_temporal;\n",
    )
    document(
        "identity",
        "realm.json",
        realm(config, bootstrap if bootstrap["status"] != "complete" else None, owner_password),
    )
    put("identity", "database-password", passwords["keycloak"])
    if bootstrap["status"] != "complete":
        put("identity", "bootstrap-password", passwords["keycloak_bootstrap"])
        document("api", "bootstrap.json", bootstrap)
        document("job", "bootstrap.json", bootstrap)
        put("job", "postgres-password", passwords["postgres"])
    document(
        "api",
        "application.json",
        {"identity_db_password": passwords["signal_identity"], "csrf_key": data["csrf_key"]},
    )
    full_config = {"schema_version": 1, **data["config"], "images": config.images}
    document("api", "config.json", full_config)
    document("api", "providers.json", configured)
    document("job", "config.json", full_config)
    put("job", "signal_migrator-password", passwords["signal_migrator"])
    for role in POLICIES:
        path = runtime / "api" / (role + ".json")
        if path.exists() and not restart:
            # Validate the existing protected file before preserving its one-use credential.
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                content = stream.read(131073)
            write_runtime(path, content, UIDS["api"])
            existing = json.loads(content)
            if set(existing) != {"role_id", "secret_id"} or any(
                not isinstance(value, str) or not value for value in existing.values()
            ):
                raise OperatorError("Existing workload credential rejected.")
            continue
        name = PREFIX + role
        role_id = store.request("GET", "/auth/approle/role/" + name + "/role-id", token=token)[
            "data"
        ]["role_id"]
        secret_id = store.request("POST", "/auth/approle/role/" + name + "/secret-id", token=token)[
            "data"
        ]["secret_id"]
        document("api", role + ".json", {"role_id": role_id, "secret_id": secret_id})
    # Reuse the tested HTTPS dashboard server with its established filename contract.
    for name, source in (("dashboard.pem", "server.pem"), ("dashboard-key.pem", "server-key.pem")):
        put("dashboard", "tls/" + name, tls["leaves"]["dashboard"][source])
    document("dashboard", "dashboard-origin.json", {"origin": config.origin})
    put("ingress", "public.pem", cert)
    put("ingress", "public-key.pem", key_bytes)
    put("ingress", "Caddyfile", ingress(config, configured))
    document("temporal", "self-host.yaml", temporal_configuration(passwords["temporal"]))
    put("temporal", "database-password", passwords["temporal"])
    for service in ("worker", "temporal"):
        source = tls["leaves"][service]
        put(service, "tls/client.pem", source["server.pem"])
        put(service, "tls/client-key.pem", source["server-key.pem"])
    for role, filename in (
        ("signal_scheduler", "scheduler-dsn"),
        ("signal_workflow", "workflow-dsn"),
    ):
        put(
            "worker",
            filename,
            make_conninfo(
                host="application-database",
                dbname="signal",
                user=role,
                password=passwords[role],
                sslmode="verify-full",
                sslrootcert="/run/signal-worker/tls/ca.pem",
                connect_timeout=5,
            ),
        )
