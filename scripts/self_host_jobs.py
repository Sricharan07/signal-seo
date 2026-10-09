"""One-shot private migration/first-owner jobs. Input and errors are never echoed."""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx2
import psycopg
from alembic import command
from alembic.config import Config
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from signal_api.integration_runtime import private_file
from signal_core.oidc_login import ConsumedOidcLoginAttempt, OidcClientRegistration
from signal_core.oidc_protocol import OidcTokenResponse, validate_id_token
from signal_core.owner_bootstrap import OwnerBootstrapIntent, bootstrap_owner
from signal_core.self_host_config import CLIENT, SelfHostConfig, unique_object

DIRECTORY = Path("/run/signal-job")


def database_dsn(role):
    password = private_file(DIRECTORY / (role + "-password")).decode()
    return make_conninfo(
        host="application-database",
        dbname="signal",
        user=role,
        password=password,
        sslmode="verify-full",
        sslrootcert=str(DIRECTORY / "tls/ca.pem"),
        connect_timeout=5,
    )


def owner():
    proof = json.loads(sys.stdin.buffer.read(65537), object_pairs_hook=unique_object)
    if (
        not isinstance(proof, dict)
        or set(proof) != {"schema_version", "attempt_id", "id_token", "access_token", "expires_in"}
        or type(proof["schema_version"]) is not int
        or proof["schema_version"] != 1
    ):
        raise ValueError("Owner proof format rejected.")
    config = SelfHostConfig.parse(json.loads(private_file(DIRECTORY / "config.json")))
    intent_data = json.loads(private_file(DIRECTORY / "bootstrap.json"))
    registration = OidcClientRegistration(config.issuer, CLIENT, config.origin + "/auth/callback")
    with psycopg.connect(
        database_dsn("postgres"), autocommit=True, row_factory=dict_row
    ) as connection:
        row = connection.execute(
            "SELECT * FROM control.oidc_login_attempts WHERE id=%s", (UUID(proof["attempt_id"]),)
        ).fetchone()
        if row is None:
            raise ValueError("Consumed owner proof unavailable.")
        attempt = ConsumedOidcLoginAttempt(
            row["id"],
            registration,
            bytes(row["nonce_hash"]),
            row["pkce_secret_reference"],
            row["return_path"],
        )
        with httpx2.Client(
            verify=str(DIRECTORY / "tls/ca.pem"), trust_env=False, follow_redirects=False, timeout=5
        ) as client:
            with client.stream(
                "GET", "https://identity:8443/identity/realms/signal/protocol/openid-connect/certs"
            ) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 65536:
                        raise ValueError("Identity keys exceeded their bound.")
                if response.status_code != 200:
                    raise ValueError("Current identity signing keys unavailable.")
                jwks = json.loads(content, object_pairs_hook=unique_object)
        identity = validate_id_token(
            OidcTokenResponse(proof["id_token"], proof["access_token"], proof["expires_in"]),
            attempt=attempt,
            jwks=jwks,
            now=int(datetime.now(UTC).timestamp()),
        )
        connection.row_factory = psycopg.rows.tuple_row
        intent = OwnerBootstrapIntent(
            UUID(intent_data["user_id"]),
            UUID(intent_data["tenant_id"]),
            UUID(intent_data["membership_id"]),
            intent_data["subject"],
            registration,
            config.workspace_name,
            config.home_region,
            intent_data["approved_at"],
            intent_data["expires_at"],
        )
        bootstrap_owner(
            connection,
            intent=intent,
            identity=identity,
            attempt_id=attempt.id,
            nonce_hash=attempt.nonce_hash,
        )


def main():
    try:
        if sys.argv[1:] == ["migrate"]:
            os.environ["SIGNAL_MIGRATION_DSN"] = database_dsn("signal_migrator")
            command.upgrade(Config("database/alembic.ini"), "head")
        elif sys.argv[1:] == ["owner"]:
            owner()
        else:
            raise ValueError("Explicit operator job required.")
        print("Private operator job completed; no external grants created.")
        return 0
    except Exception:
        print(
            "Private operator job failed; values suppressed; reconcile before retry.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
