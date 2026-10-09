"""One human-approved test owner bootstrap, from a fresh signed OIDC assertion."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
from integration_environment import load_integration_scope

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))
sys.path.insert(0, str(ROOT / "apps/api/src"))

from integration_cleanup_incomplete_identity import remote  # noqa: E402
from integration_secrets import (  # noqa: E402
    OperatorError,
    private_directory,
    read_private,
    write_private,
)
from psycopg import sql  # noqa: E402
from signal_api.test_identity_proof import CLIENT  # noqa: E402
from signal_core.oidc_login import ConsumedOidcLoginAttempt, OidcClientRegistration  # noqa: E402
from signal_core.oidc_protocol import OidcTokenResponse, validate_id_token  # noqa: E402
from signal_core.session_issuance import SessionPolicy, _validate_verified_identity  # noqa: E402

DATABASE = "signal-integration-application-application-database-1"
APPLICATION_CONTAINER = "signal-integration-application-api-1"
POLICY = SessionPolicy(
    primary_acr_values=frozenset({"0"}),
    mfa_acr_values=frozenset({"1", "2"}),
    required_mfa_methods=frozenset({"otp"}),
    maximum_auth_age_seconds=300,
)


def database(statement):
    return remote(
        [
            "sudo",
            "docker",
            "exec",
            "-i",
            "-u",
            "70",
            DATABASE,
            "psql",
            "-X",
            "-U",
            "postgres",
            "-d",
            "signal",
            "-Atq",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        statement,
    )


def validate_proof(proof, row, jwks, approval, *, now=None):
    current = now or datetime.now(UTC)
    if (
        not isinstance(proof, dict)
        or set(proof) != {"schema_version", "attempt_id", "id_token", "access_token", "expires_in"}
        or type(proof["schema_version"]) is not int
        or proof["schema_version"] != 1
        or set(approval) != {"approved_at", "expires_at"}
        or any(type(value) is not int for value in approval.values())
        or not approval["approved_at"] <= int(current.timestamp()) < approval["expires_at"]
        or not 0 < approval["expires_at"] - approval["approved_at"] <= 3600
        or row["id"] != proof["attempt_id"]
        or row["purpose"] != "login"
        or row["oidc_issuer"] != load_integration_scope().issuer
        or row["client_id"] != CLIENT
        or row["redirect_uri"] != load_integration_scope().origin + "/auth/callback"
    ):
        raise OperatorError("Signed test proof binding rejected.")
    created = datetime.fromisoformat(row["created_at"])
    consumed = datetime.fromisoformat(row["consumed_at"])
    expiry = datetime.fromisoformat(row["expires_at"])
    approved = datetime.fromtimestamp(approval["approved_at"], UTC)
    if not approved <= created <= consumed <= current < expiry:
        raise OperatorError("Signed test proof is stale or not consumed.")
    attempt = ConsumedOidcLoginAttempt(
        UUID(row["id"]),
        OidcClientRegistration(load_integration_scope().issuer, CLIENT, row["redirect_uri"]),
        bytes.fromhex(row["nonce_hash"]),
        row["pkce_secret_reference"],
        row["return_path"],
    )
    identity = validate_id_token(
        OidcTokenResponse(proof["id_token"], proof["access_token"], proof["expires_in"]),
        attempt=attempt,
        jwks=jwks,
        now=int(current.timestamp()),
    )
    if (
        identity.subject != load_integration_scope().owner_subject
        or identity.verified_email != load_integration_scope().owner_email
    ):
        raise OperatorError("Signed test owner identity rejected.")
    if identity.issued_at < approval["approved_at"]:
        raise OperatorError("Signed test proof predates approval.")
    if _validate_verified_identity(identity, POLICY, current)[0] != "mfa":
        raise OperatorError("Fresh signed OTP proof is required.")
    return identity


def bootstrap_sql(intent, *, rehearse):
    def literal(value):
        return sql.Literal(value).as_string()

    user, tenant, membership, attempt = (
        literal(intent[key]) + "::uuid"
        for key in ("user_id", "tenant_id", "membership_id", "attempt_id")
    )
    approval = literal(intent["approved_at"]) + "::bigint"
    nonce = literal(intent["nonce_hash"])
    guard = (
        "BEGIN IF EXISTS(SELECT 1 FROM control.users) OR EXISTS(SELECT 1 FROM app.tenants) "
        "OR EXISTS(SELECT 1 FROM app.memberships) "
        "OR EXISTS(SELECT 1 FROM control.tenant_directory) "
        "THEN RAISE EXCEPTION 'Test bootstrap is create-only'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM control.oidc_login_attempts WHERE id="
        + attempt
        + " AND oidc_issuer="
        + literal(load_integration_scope().issuer)
        + " AND client_id="
        + literal(CLIENT)
        + " AND purpose='login' AND encode(nonce_hash,'hex')="
        + nonce
        + " AND created_at>=to_timestamp("
        + approval
        + ") AND consumed_at IS NOT NULL "
        "AND consumed_at<=now() AND expires_at>now() AND consumed_at>now()-interval '5 minutes') "
        "THEN RAISE EXCEPTION 'Fresh consumed proof required'; END IF; END"
    )
    return (
        "BEGIN;SET LOCAL lock_timeout='5s';SET LOCAL statement_timeout='10s';"
        "LOCK TABLE control.users,app.tenants,app.memberships,control.tenant_directory,"
        "control.oidc_login_attempts IN SHARE ROW EXCLUSIVE MODE;DO " + literal(guard) + ";"
        "INSERT INTO control.users(id,oidc_issuer,oidc_subject,display_name,contact_email) VALUES("
        + user
        + ","
        + literal(load_integration_scope().issuer)
        + ","
        + literal(load_integration_scope().owner_subject)
        + ",'Test owner',"
        + literal(load_integration_scope().owner_email)
        + ");"
        "SET LOCAL ROLE signal_bootstrap;SELECT set_config('signal.tenant_id',"
        + literal(intent["tenant_id"])
        + ",true);"
        "INSERT INTO app.tenants(tenant_id,name,home_region) VALUES("
        + tenant
        + ","
        + literal(load_integration_scope().tenant_name)
        + ","
        + literal(load_integration_scope().home_region)
        + ");"
        "INSERT INTO control.tenant_directory VALUES(" + tenant + ",'active',1);"
        "INSERT INTO app.memberships"
        "(tenant_id,id,user_id,role_key,state,authorization_epoch) VALUES("
        + tenant
        + ","
        + membership
        + ","
        + user
        + ",'owner','active',1);RESET ROLE;"
        "SELECT jsonb_build_object('users',(SELECT count(*) FROM control.users),'tenants',"
        "(SELECT count(*) FROM app.tenants),'memberships',(SELECT count(*) FROM app.memberships));"
        + ("ROLLBACK;" if rehearse else "COMMIT;")
    )


def bootstrap(directory, approval_path, *, approved):
    if approved is not True:
        raise OperatorError("Exact human bootstrap approval is required.")
    directory = private_directory(directory)
    if any(directory.iterdir()):
        raise OperatorError("Use an empty protected audit directory; never retry unknown outcomes.")
    approval = json.loads(read_private(approval_path))
    proof = json.loads(
        remote(
            [
                "sudo",
                "docker",
                "exec",
                "-u",
                "10001",
                APPLICATION_CONTAINER,
                "python",
                "-c",
                "from signal_api.integration_runtime import private_file;"
                "from pathlib import Path;"
                "print(private_file(Path('/tmp/signal-owner-proof.json')).decode())",
            ]
        )
    )
    identifier = UUID(proof["attempt_id"])
    row = json.loads(
        database(
            "SELECT to_jsonb(a)||jsonb_build_object('nonce_hash',encode(nonce_hash,'hex')) "
            "FROM control.oidc_login_attempts a WHERE id="
            + sql.Literal(str(identifier)).as_string()
            + "::uuid;"
        )
    )
    with httpx2.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        response = client.get(load_integration_scope().issuer + "/protocol/openid-connect/certs")
        if response.status_code != 200 or len(response.content) > 65536:
            raise OperatorError("Current identity signing keys unavailable.")
        jwks = response.json()
    identity = validate_proof(proof, row, jwks, approval)
    intent = {
        "schema_version": 1,
        "human_approval": "isolated Signal Test owner; no standing grants",
        "approved_at": approval["approved_at"],
        "attempt_id": str(identifier),
        "nonce_hash": row["nonce_hash"],
        "signed_assertion_sha256": hashlib.sha256(proof["id_token"].encode()).hexdigest(),
        "user_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "membership_id": str(uuid4()),
        "verified_subject": identity.subject,
        "issuer": identity.issuer,
        "authentication_context": identity.authentication_context,
        "authentication_methods": sorted(identity.authentication_methods),
        "auth_time": identity.auth_time,
    }
    write_private(directory / "intent.json", json.dumps(intent, sort_keys=True).encode())
    database(bootstrap_sql(intent, rehearse=True))
    # Repeat signature/freshness verification immediately before the committing transaction.
    validate_proof(proof, row, jwks, approval)
    database(bootstrap_sql(intent, rehearse=False))
    outcome = json.loads(
        database(
            "SELECT jsonb_build_object('users',(SELECT count(*) FROM control.users),"
            "'tenants',(SELECT count(*) FROM app.tenants),"
            "'memberships',(SELECT count(*) FROM app.memberships));"
        )
    )
    if outcome != {"users": 1, "tenants": 1, "memberships": 1}:
        raise OperatorError("Bootstrap outcome unknown; inspect prepared intent, do not retry.")
    write_private(
        directory / "outcome.json",
        json.dumps(
            {
                "status": "PASS",
                "rollback_rehearsal": "PASS",
                "fresh_signed_otp": True,
                "user_id": intent["user_id"],
                "tenant_id": intent["tenant_id"],
                "standing_authorizations_created": 0,
                "sites_created": 0,
                "public_api_provisioning_privileges": False,
            }
        ).encode(),
    )
    remote(
        [
            "sudo",
            "docker",
            "exec",
            "-u",
            "10001",
            APPLICATION_CONTAINER,
            "python",
            "-c",
            "import os;from pathlib import Path;"
            "f=os.open('/tmp/signal-owner-proof.done',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);"
            "os.close(f);Path('/tmp/signal-owner-proof.json').unlink()",
        ]
    )
    print("Fresh signed OTP verified; isolated test owner/workspace created. No external grants.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--approval-file", type=Path, required=True)
    parser.add_argument("--human-approved", action="store_true")
    args = parser.parse_args()
    try:
        bootstrap(args.directory, args.approval_file, approved=args.human_approved)
        return 0
    except Exception as error:
        print(
            f"Test owner bootstrap incomplete ({type(error).__name__}); values suppressed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
