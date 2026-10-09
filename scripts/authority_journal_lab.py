"""Disposable separate PostgreSQL journal, primary restore, and OpenBao drill."""

import asyncio
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
import psycopg
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from database_lab import docker, isolated_postgres, provision
from openbao_lab import AUTHORITY_MOUNT, isolated_openbao
from openbao_lab import provision as provision_openbao
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.errors import InsufficientPrivilege, UniqueViolation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.authority_journal import (  # noqa: E402
    AuthorityJournal,
    AuthorityJournalConflict,
    AuthorityJournalUnavailable,
    RestrictionRecord,
    dispatch_pending_restrictions,
    replay_after_restore,
)
from signal_core.invitation_acceptance import (  # noqa: E402
    InvitationAcceptanceDenied,
    accept_site_invitation,
)
from signal_core.invitation_identity_proofs import issue_invitation_identity_proof  # noqa: E402
from signal_core.oidc_protocol import VerifiedOidcIdentity  # noqa: E402
from signal_core.recipe_releases import (  # noqa: E402
    VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
    transition_recipe_release,
)
from signal_core.session_management import revoke_browser_session  # noqa: E402
from signal_core.standing_authorization import (  # noqa: E402
    RecipeRange,
    StandingGrantRequest,
    grant_standing_authorization,
    revoke_standing_authorization,
)
from signal_core.team_invitations import (  # noqa: E402
    issue_owner_team_invitation,
    read_owner_team,
    revoke_owner_team_invitation,
)
from signal_core.write_intent_journal import (  # noqa: E402
    WriteIntentJournal,
    WriteIntentJournalConflict,
    WriteIntentRecord,
)


def _run(*args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["docker", *args], input=input_bytes, capture_output=True, timeout=120, check=False
    )
    if result.returncode:
        raise RuntimeError(f"Docker {args[0]} failed during authority restore drill.")
    return result.stdout


def _container_for_port(port: str) -> str:
    names = docker(
        "container", "ls", "--filter", "label=io.signal.lab.run", "--format", "{{.Names}}"
    )
    for name in names.splitlines():
        if port in docker("port", name, "5432/tcp"):
            return name
    raise RuntimeError("Primary lab container not found.")


def _journal_credentials(admin_dsn: str, common: dict[str, str]) -> tuple[str, str]:
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute((ROOT / "database/authority_journal.sql").read_text())
        result = []
        for role in ("signal_journal_writer", "signal_journal_reader"):
            password = secrets.token_hex(32)
            admin.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            admin.execute(
                sql.SQL("GRANT CONNECT ON DATABASE signal_test TO {}").format(sql.Identifier(role))
            )
            result.append(make_conninfo(**common, user=role, password=password))
    return result[0], result[1]


def _seed_session(admin: psycopg.Connection, generation: str) -> tuple[UUID, UUID, str]:
    user_id, session_id = uuid4(), uuid4()
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    admin.execute(
        "INSERT INTO control.users (id, oidc_issuer, oidc_subject, display_name) "
        "VALUES (%s, 'https://identity.example.invalid', %s, 'Journal lab user')",
        (user_id, str(user_id)),
    )
    admin.execute(
        "INSERT INTO control.identity_sessions "
        "(id, user_id, token_hash, auth_time, authentication_level, recovery_generation, "
        "expires_at, last_seen_at) VALUES (%s,%s,%s,%s,'primary',%s,%s,%s)",
        (
            session_id,
            user_id,
            sha256(token.encode()).digest(),
            now,
            generation,
            now + timedelta(hours=1),
            now,
        ),
    )
    return user_id, session_id, token


def _seed_standing_site(
    admin: psycopg.Connection, user_id: UUID, generation: str
) -> tuple[UUID, UUID, str]:
    tenant_id, site_id = uuid4(), uuid4()
    session_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    identity_id = uuid4()
    admin.execute(
        "INSERT INTO control.identity_sessions "
        "(id,user_id,token_hash,auth_time,authentication_level,recovery_generation,"
        "expires_at,last_seen_at) VALUES (%s,%s,%s,%s,'primary',%s,%s,%s)",
        (
            identity_id,
            user_id,
            sha256(secrets.token_bytes(32)).digest(),
            now,
            generation,
            now + timedelta(hours=2),
            now,
        ),
    )
    admin.execute(
        "INSERT INTO app.tenants (tenant_id,name,home_region) "
        "VALUES (%s,'Synthetic standing lab','test')",
        (tenant_id,),
    )
    admin.execute(
        "INSERT INTO control.tenant_directory VALUES (%s,'active',1)",
        (tenant_id,),
    )
    admin.execute(
        "INSERT INTO app.sites (tenant_id,id,name,primary_origin,timezone,"
        "reporting_currency,state,ownership_status) "
        "VALUES (%s,%s,'Synthetic standing site','https://example.invalid',"
        "'UTC','USD','active','verified')",
        (tenant_id, site_id),
    )
    admin.execute(
        "INSERT INTO app.memberships (tenant_id,id,user_id,role_key,state,authorization_epoch) "
        "VALUES (%s,%s,%s,'owner','active',1)",
        (tenant_id, uuid4(), user_id),
    )
    admin.execute(
        "INSERT INTO app.site_memberships (tenant_id,site_id,id,user_id,permission_set,"
        "authorization_epoch,state) VALUES (%s,%s,%s,%s,%s,1,'active')",
        (
            tenant_id,
            site_id,
            uuid4(),
            user_id,
            '{"permissions":["site.snapshot.request"],"schema_version":1}',
        ),
    )
    admin.execute(
        "INSERT INTO app.sessions (tenant_id,id,identity_session_id,user_id,"
        "session_token_hash,auth_time,mfa_level,expires_at,last_seen_at,"
        "active_site_id,session_version) "
        "VALUES (%s,%s,%s,%s,%s,%s,'primary',%s,%s,%s,2)",
        (
            tenant_id,
            uuid4(),
            identity_id,
            user_id,
            sha256(session_token.encode()).digest(),
            now,
            now + timedelta(hours=2),
            now,
            site_id,
        ),
    )
    return tenant_id, site_id, session_token


def _refresh_standing_session(
    admin: psycopg.Connection,
    tenant_id: UUID,
    site_id: UUID,
    user_id: UUID,
    generation: str,
) -> str:
    now = datetime.now(UTC)
    identity_id = uuid4()
    token = secrets.token_urlsafe(32)
    admin.execute(
        "INSERT INTO control.identity_sessions "
        "(id,user_id,token_hash,auth_time,authentication_level,recovery_generation,"
        "expires_at,last_seen_at) VALUES (%s,%s,%s,%s,'primary',%s,%s,%s)",
        (
            identity_id,
            user_id,
            sha256(secrets.token_bytes(32)).digest(),
            now,
            generation,
            now + timedelta(hours=2),
            now,
        ),
    )
    admin.execute(
        "INSERT INTO app.sessions (tenant_id,id,identity_session_id,user_id,"
        "session_token_hash,auth_time,mfa_level,expires_at,last_seen_at,"
        "active_site_id,session_version) "
        "VALUES (%s,%s,%s,%s,%s,%s,'primary',%s,%s,%s,2)",
        (
            tenant_id,
            uuid4(),
            identity_id,
            user_id,
            sha256(token.encode()).digest(),
            now,
            now + timedelta(hours=2),
            now,
            site_id,
        ),
    )
    return token


def _expect_raises(expected: type[Exception], function, *args, **kwargs) -> Exception:
    try:
        function(*args, **kwargs)
    except expected as error:
        return error
    raise AssertionError(f"Expected {expected.__name__}.")


def _qualify_invitation_revocation(
    primary_admin_dsn,
    primary_env,
    primary_container,
    primary_admin,
    identity,
    scheduler,
    journal,
    unavailable_journal,
    reader,
    key,
    encryption_key,
    recovery_authority,
    bao,
    user_id,
    tenant_id,
    site_id,
) -> int:
    generation = asyncio.run(recovery_authority.current_generation(verify=bao.tls_context)).value
    token = _refresh_standing_session(primary_admin, tenant_id, site_id, user_id, generation)
    primary_admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=("
        "SELECT identity_session_id FROM app.sessions WHERE session_token_hash=%s)",
        (sha256(token.encode()).digest(),),
    )
    primary_admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE session_token_hash=%s",
        (sha256(token.encode()).digest(),),
    )
    owner = dict(session_token=token, site_id=site_id, current_recovery_generation=generation)
    invitation = issue_owner_team_invitation(
        identity, **owner, email="invitation-journal@example.test", role_key="viewer"
    )
    snapshot = _run(
        "exec",
        "-u",
        "postgres",
        primary_container,
        "pg_dump",
        "-U",
        "postgres",
        "-Fc",
        "signal_test",
    )
    event_id = uuid4()
    result = revoke_owner_team_invitation(
        identity, **owner, invitation_id=invitation.id, event_id=event_id
    )
    assert result["durability"] == "AUTHORITY_DURABILITY_PENDING"
    _expect_raises(
        AuthorityJournalUnavailable, dispatch_pending_restrictions, scheduler, unavailable_journal
    )
    team = read_owner_team(identity, **owner)
    revoked = next(item for item in team["invitations"] if item["id"] == str(invitation.id))
    assert (revoked["state"], revoked["revocation_durability"]) == ("revoked", "pending")
    assert dispatch_pending_restrictions(scheduler, journal) == 1
    assert dispatch_pending_restrictions(scheduler, journal) == 0
    team = read_owner_team(identity, **owner)
    revoked = next(item for item in team["invitations"] if item["id"] == str(invitation.id))
    assert revoked["revocation_durability"] == "acknowledged"
    record, receipt = next(
        entry for entry in journal.verify_stream().entries if entry[0].event_id == event_id
    )
    assert (record.target_kind, record.restriction_kind, record.target_id) == (
        "invitation",
        "invitation_revoked",
        invitation.id,
    )
    assert invitation.token.encode() not in record.canonical_body()
    assert invitation.email_normalized.encode() not in record.canonical_body()
    assert journal.append(record).position == receipt.position
    print("PASS invitation revocation stays local during outage; independent receipt confirms it")

    _run(
        "exec",
        "-u",
        "postgres",
        primary_container,
        "createdb",
        "-U",
        "postgres",
        "signal_invitation_restore",
    )
    _run(
        "exec",
        "-i",
        "-u",
        "postgres",
        primary_container,
        "pg_restore",
        "-U",
        "postgres",
        "--exit-on-error",
        "-d",
        "signal_invitation_restore",
        input_bytes=snapshot,
    )
    restored_admin_dsn = make_conninfo(primary_admin_dsn, dbname="signal_invitation_restore")
    restored_dispatcher_dsn = make_conninfo(
        primary_env["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"], dbname="signal_invitation_restore"
    )
    restored_identity_dsn = make_conninfo(
        primary_env["SIGNAL_TEST_IDENTITY_DSN"], dbname="signal_invitation_restore"
    )
    with psycopg.connect(restored_admin_dsn, autocommit=True) as admin:
        admin.execute(
            "GRANT CONNECT ON DATABASE signal_invitation_restore "
            "TO signal_authority_dispatcher,signal_identity"
        )
        assert admin.execute(
            "SELECT revoked_at,consumed_at FROM app.invitations WHERE id=%s", (invitation.id,)
        ).fetchone() == (None, None)
    with httpx2.Client(
        base_url=bao.base_url,
        verify=bao.tls_context,
        trust_env=False,
        headers={"X-Vault-Token": bao.root_token, "Accept": "application/json"},
    ) as root:
        response = root.post(
            f"/v1/{AUTHORITY_MOUNT}/data/recovery/current",
            json={
                "options": {"cas": 4},
                "data": {"generation": "after-invitation-restore-" + uuid4().hex},
            },
        )
        assert response.status_code == 200
    with psycopg.connect(restored_dispatcher_dsn, autocommit=True) as restored:
        stream = asyncio.run(
            replay_after_restore(
                restored,
                AuthorityJournal(reader, key.public_key(), encryption_key),
                recovery_authority,
                backup_recovery_generation=generation,
                verify=bao.tls_context,
            )
        )
        assert any(entry[0].event_id == event_id for entry in stream.entries)
    with (
        psycopg.connect(restored_admin_dsn, autocommit=True) as admin,
        psycopg.connect(restored_identity_dsn, autocommit=True) as restored_identity,
    ):
        assert admin.execute(
            "SELECT target_kind,restriction_kind FROM control.authority_denial_tombstones "
            "WHERE event_id=%s",
            (event_id,),
        ).fetchone() == ("invitation", "invitation_revoked")
        before = admin.execute(
            "SELECT count(*) FROM app.memberships WHERE tenant_id=%s", (tenant_id,)
        ).fetchone()
        timestamp = int(datetime.now(UTC).timestamp())
        proof = issue_invitation_identity_proof(
            restored_identity,
            identity=VerifiedOidcIdentity(
                issuer="https://identity.example.invalid",
                subject="invited-" + uuid4().hex,
                client_id="signal-dashboard",
                issued_at=timestamp,
                expires_at=timestamp + 300,
                auth_time=timestamp,
                provider_session_id="synthetic-journal-lab",
                authentication_context="1",
                verified_email=invitation.email_normalized,
            ),
        )
        _expect_raises(
            InvitationAcceptanceDenied,
            accept_site_invitation,
            restored_identity,
            identity_proof_token=proof.token,
            invitation_id=invitation.id,
            token=invitation.token,
            display_name="Synthetic invited teammate",
        )
        assert admin.execute(
            "SELECT revoked_at,consumed_at FROM app.invitations WHERE id=%s", (invitation.id,)
        ).fetchone() == (None, None)
        assert (
            admin.execute(
                "SELECT count(*) FROM app.memberships WHERE tenant_id=%s", (tenant_id,)
            ).fetchone()
            == before
        )
    print("PASS real invitation snapshot restore, OpenBao rotation and denial without a grant")
    return 2


def main() -> int:
    passed = 0
    with isolated_postgres(minimum_free=2 * 1024**3) as (primary_admin_dsn, primary_common):
        primary_env, _ = provision(primary_admin_dsn, primary_common)
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=primary_env,
            cwd=ROOT,
            check=True,
            timeout=180,
        )
        primary_container = _container_for_port(primary_common["port"])
        with isolated_postgres(minimum_free=2 * 1024**3) as (journal_admin_dsn, journal_common):
            writer_dsn, reader_dsn = _journal_credentials(journal_admin_dsn, journal_common)
            with isolated_openbao() as bao:
                _, _, recovery_authority, backup_generation = provision_openbao(bao)
                key = Ed25519PrivateKey.generate()
                encryption_key = secrets.token_bytes(64)
                with (
                    psycopg.connect(primary_admin_dsn, autocommit=True) as primary_admin,
                    psycopg.connect(
                        primary_env["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True
                    ) as identity,
                    psycopg.connect(
                        primary_env["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"], autocommit=True
                    ) as scheduler,
                    psycopg.connect(writer_dsn, autocommit=True) as writer,
                    psycopg.connect(reader_dsn, autocommit=True) as reader,
                    psycopg.connect(journal_admin_dsn, autocommit=True) as journal_admin,
                ):
                    journal = AuthorityJournal(writer, key.public_key(), encryption_key, key)
                    write_key = Ed25519PrivateKey.generate()
                    write_journal = WriteIntentJournal(
                        writer, write_key.public_key(), secrets.token_bytes(64), write_key
                    )
                    write_operation_id = uuid4()
                    write_record = WriteIntentRecord(
                        write_operation_id,
                        uuid4(),
                        123,
                        "a" * 64,
                        "b" * 64,
                        "c" * 40,
                        "d" * 64,
                        f"signal/{write_operation_id.hex}",
                        "e" * 64,
                    )
                    first_write = write_journal.append(write_record)
                    second_write = write_journal.append(write_record)
                    assert (
                        second_write.generation,
                        second_write.position,
                        second_write.body_hash,
                    ) == (first_write.generation, first_write.position, first_write.body_hash)
                    assert write_journal.verify_stream()[1][0][0] == write_record
                    passed += 1
                    print("PASS independent write intent append and exact replay")
                    _expect_raises(
                        WriteIntentJournalConflict,
                        write_journal.append,
                        WriteIntentRecord(
                            write_record.operation_id,
                            write_record.site_id,
                            124,
                            write_record.revision_sha256,
                            write_record.intent_sha256,
                            write_record.base_sha,
                            write_record.patch_sha256,
                            write_record.branch_name,
                            write_record.recovery_plan_sha256,
                        ),
                    )
                    passed += 1
                    print("PASS write intent identity conflict denied")
                    _expect_raises(
                        InsufficientPrivilege,
                        reader.execute,
                        "SELECT * FROM write_journal.append(%s,%s,%s)",
                        (uuid4(), b"fake", bytes(64)),
                    )
                    passed += 1
                    print("PASS write intent append unavailable to reader")
                    user_id, session_id, token = _seed_session(primary_admin, backup_generation)
                    snapshot = _run(
                        "exec",
                        "-u",
                        "postgres",
                        primary_container,
                        "pg_dump",
                        "-U",
                        "postgres",
                        "-Fc",
                        "signal_test",
                    )
                    assert snapshot.startswith(b"PGDMP")
                    event_id = uuid4()
                    assert revoke_browser_session(
                        identity,
                        session_token=token,
                        presented_session_kind="identity",
                        event_id_factory=lambda: event_id,
                    )
                    assert primary_admin.execute(
                        "SELECT revoked_at IS NOT NULL FROM control.identity_sessions WHERE id=%s",
                        (session_id,),
                    ).fetchone() == (True,)
                    assert scheduler.execute(
                        "SELECT control.authority_durability_status(%s)", (event_id,)
                    ).fetchone() == ("AUTHORITY_DURABILITY_PENDING",)
                    with psycopg.connect(
                        primary_env["SIGNAL_TEST_SCHEDULER_DSN"], autocommit=True
                    ) as ordinary_scheduler:
                        _expect_raises(
                            InsufficientPrivilege,
                            ordinary_scheduler.execute,
                            "SELECT * FROM control.pending_authority_restrictions(1)",
                        )
                    passed += 1
                    print("PASS local restriction is effective and durability is pending")

                    with psycopg.connect(writer_dsn, autocommit=True) as offline:
                        unavailable_journal = AuthorityJournal(
                            offline, key.public_key(), encryption_key, key
                        )
                    _expect_raises(
                        AuthorityJournalUnavailable,
                        dispatch_pending_restrictions,
                        scheduler,
                        unavailable_journal,
                    )
                    assert scheduler.execute(
                        "SELECT control.authority_durability_status(%s)", (event_id,)
                    ).fetchone() == ("AUTHORITY_DURABILITY_PENDING",)
                    assert primary_admin.execute(
                        "SELECT revoked_at IS NOT NULL FROM control.identity_sessions WHERE id=%s",
                        (session_id,),
                    ).fetchone() == (True,)
                    pending = scheduler.execute(
                        "SELECT * FROM control.pending_authority_restrictions(1)"
                    ).fetchone()
                    assert pending is not None and pending[0] == event_id
                    accepted_without_local_receipt = journal.append(
                        RestrictionRecord(
                            event_id,
                            user_id,
                            session_id,
                            pending[3],
                            pending[4],
                            sha256(rfc8785.dumps(pending[5])).hexdigest(),
                        )
                    )
                    assert accepted_without_local_receipt.position == 1
                    assert scheduler.execute(
                        "SELECT control.authority_durability_status(%s)", (event_id,)
                    ).fetchone() == ("AUTHORITY_DURABILITY_PENDING",)
                    passed += 1
                    print("PASS outage keeps restriction local; lost receipt retries exact event")

                    assert dispatch_pending_restrictions(scheduler, journal) == 1
                    assert dispatch_pending_restrictions(scheduler, journal) == 0
                    assert scheduler.execute(
                        "SELECT control.authority_durability_status(%s)", (event_id,)
                    ).fetchone() == ("ACKNOWLEDGED",)
                    assert primary_admin.execute(
                        "SELECT count(*) FROM control.platform_events "
                        "WHERE event_type='authority.restriction.acknowledged' AND object_id=%s",
                        (event_id,),
                    ).fetchone() == (1,)
                    stream = journal.verify_stream()
                    record, receipt = stream.entries[0]
                    assert scheduler.execute(
                        "SELECT control.record_authority_restriction_receipt(%s,%s,%s,%s,%s,%s)",
                        (
                            uuid4(),
                            event_id,
                            receipt.generation,
                            receipt.position,
                            receipt.body_hash,
                            datetime.now(UTC),
                        ),
                    ).fetchone() == (False,)
                    _expect_raises(
                        UniqueViolation,
                        scheduler.execute,
                        "SELECT control.record_authority_restriction_receipt(%s,%s,%s,%s,%s,%s)",
                        (
                            uuid4(),
                            event_id,
                            receipt.generation,
                            receipt.position,
                            "f" * 64,
                            datetime.now(UTC),
                        ),
                    )
                    encrypted_body = journal_admin.execute(
                        "SELECT body FROM authority_journal.entries WHERE position=1"
                    ).fetchone()[0]
                    assert bytes(encrypted_body) != record.canonical_body()
                    assert b"identity_session" not in bytes(encrypted_body)
                    assert journal.append(record).position == receipt.position
                    _expect_raises(
                        AuthorityJournalConflict,
                        journal.append,
                        RestrictionRecord(
                            event_id, user_id, uuid4(), 1, record.event_time, record.payload_hash
                        ),
                    )
                    passed += 1
                    print("PASS independent append, verified receipt, dedup, conflicting ID denial")

                    try:
                        with writer.transaction():
                            writer.execute("DELETE FROM authority_journal.entries")
                    except InsufficientPrivilege:
                        pass
                    else:
                        raise AssertionError("Journal writer acquired mutation authority.")
                    with psycopg.connect(writer_dsn) as non_autocommit:
                        _expect_raises(
                            ValueError,
                            AuthorityJournal,
                            non_autocommit,
                            key.public_key(),
                            encryption_key,
                            key,
                        )
                    missing = RestrictionRecord(
                        uuid4(),
                        user_id,
                        uuid4(),
                        1,
                        datetime.now(UTC),
                        sha256(b"absent").hexdigest(),
                    )
                    journal.append(missing)
                    passed += 1
                    print("PASS append-only writer role and typed absent-target event")

                    _run(
                        "exec",
                        "-u",
                        "postgres",
                        primary_container,
                        "createdb",
                        "-U",
                        "postgres",
                        "signal_restore",
                    )
                    _run(
                        "exec",
                        "-i",
                        "-u",
                        "postgres",
                        primary_container,
                        "pg_restore",
                        "-U",
                        "postgres",
                        "--exit-on-error",
                        "-d",
                        "signal_restore",
                        input_bytes=snapshot,
                    )
                    restored_admin_dsn = make_conninfo(primary_admin_dsn, dbname="signal_restore")
                    restored_scheduler_dsn = make_conninfo(
                        primary_env["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"], dbname="signal_restore"
                    )
                    with psycopg.connect(restored_admin_dsn, autocommit=True) as restored_admin:
                        restored_admin.execute(
                            "GRANT CONNECT ON DATABASE signal_restore "
                            "TO signal_authority_dispatcher"
                        )
                        assert restored_admin.execute(
                            "SELECT revoked_at FROM control.identity_sessions WHERE id=%s",
                            (session_id,),
                        ).fetchone() == (None,)
                    with psycopg.connect(restored_scheduler_dsn, autocommit=True) as restored:
                        stale_generation = _expect_raises(
                            AuthorityJournalUnavailable,
                            asyncio.run,
                            replay_after_restore(
                                restored,
                                AuthorityJournal(reader, key.public_key(), encryption_key),
                                recovery_authority,
                                backup_recovery_generation=backup_generation,
                                verify=bao.tls_context,
                            ),
                        )
                        assert "not rotated" in str(stale_generation)
                    with httpx2.Client(
                        base_url=bao.base_url,
                        verify=bao.tls_context,
                        trust_env=False,
                        headers={
                            "X-Vault-Token": bao.root_token,
                            "Accept": "application/json",
                        },
                    ) as root:
                        response = root.post(
                            f"/v1/{AUTHORITY_MOUNT}/data/recovery/current",
                            json={
                                "options": {"cas": 1},
                                "data": {"generation": "after-restore-" + uuid4().hex},
                            },
                        )
                        assert response.status_code == 200
                    with psycopg.connect(restored_scheduler_dsn, autocommit=True) as restored:
                        stream = asyncio.run(
                            replay_after_restore(
                                restored,
                                AuthorityJournal(reader, key.public_key(), encryption_key),
                                recovery_authority,
                                backup_recovery_generation=backup_generation,
                                verify=bao.tls_context,
                            )
                        )
                        assert stream.head_position == 2
                    with psycopg.connect(restored_admin_dsn, autocommit=True) as restored_admin:
                        assert restored_admin.execute(
                            "SELECT revoked_at IS NOT NULL FROM control.identity_sessions "
                            "WHERE id=%s",
                            (session_id,),
                        ).fetchone() == (True,)
                        assert restored_admin.execute(
                            "SELECT count(*) FROM control.authority_denial_tombstones"
                        ).fetchone() == (2,)
                        assert restored_admin.execute(
                            "SELECT count(*) FROM control.authority_replay_checkpoints"
                        ).fetchone() == (1,)
                        _expect_raises(
                            InsufficientPrivilege,
                            restored_admin.execute,
                            "UPDATE control.identity_sessions SET revoked_at=NULL WHERE id=%s",
                            (session_id,),
                        )
                        now = datetime.now(UTC)
                        _expect_raises(
                            InsufficientPrivilege,
                            restored_admin.execute,
                            "INSERT INTO control.identity_sessions "
                            "(id,user_id,token_hash,auth_time,authentication_level,"
                            "recovery_generation,expires_at,last_seen_at) "
                            "VALUES (%s,%s,%s,%s,'primary',%s,%s,%s)",
                            (
                                missing.target_id,
                                user_id,
                                sha256(b"new-token").digest(),
                                now,
                                backup_generation,
                                now + timedelta(hours=1),
                                now,
                            ),
                        )
                    passed += 1
                    print("PASS real primary snapshot restore, OpenBao rotation, deny-only replay")

                    standing_generation = asyncio.run(
                        recovery_authority.current_generation(verify=bao.tls_context)
                    ).value
                    standing_tenant, standing_site, standing_token = _seed_standing_site(
                        primary_admin, user_id, standing_generation
                    )
                    with psycopg.connect(
                        primary_env["SIGNAL_TEST_API_DSN"], autocommit=True
                    ) as standing_api:
                        standing_grant = grant_standing_authorization(
                            standing_api,
                            session_token=standing_token,
                            current_recovery_generation=standing_generation,
                            request=StandingGrantRequest(
                                site_id=standing_site,
                                recipe_ranges=(
                                    RecipeRange("title_description_improvement", "1.0.0", "2.0.0"),
                                ),
                                thresholds={"draft_patch": 0.9},
                                weekly_volume_caps={"draft_patch": 1},
                                weekly_total_cap=1,
                                weekly_spend_cents=0,
                                excluded_paths=("/private",),
                                starts_at=datetime.now(UTC),
                                ends_at=datetime.now(UTC) + timedelta(days=7),
                                recovery_window_hours=24,
                            ),
                        )
                    assert standing_grant.recipe_release_ids == (
                        VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
                    )
                    recipe_snapshot = _run(
                        "exec",
                        "-u",
                        "postgres",
                        primary_container,
                        "pg_dump",
                        "-U",
                        "postgres",
                        "-Fc",
                        "signal_test",
                    )
                    recipe_backup_generation = asyncio.run(
                        recovery_authority.current_generation(verify=bao.tls_context)
                    ).value
                    recipe_event_id = uuid4()
                    with psycopg.connect(
                        primary_env["SIGNAL_TEST_RELEASE_MANAGER_DSN"], autocommit=True
                    ) as release_manager:
                        assert (
                            transition_recipe_release(
                                release_manager,
                                release_id=VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
                                expected_status="REVIEWED",
                                new_status="REVOKED",
                                actor_user_id=user_id,
                                reason="Lab review withdrawal",
                                event_id=recipe_event_id,
                            )
                            == 4
                        )
                    assert scheduler.execute(
                        "SELECT control.authority_durability_status(%s)", (recipe_event_id,)
                    ).fetchone() == ("AUTHORITY_DURABILITY_PENDING",)
                    assert dispatch_pending_restrictions(scheduler, journal) == 1
                    assert journal.verify_stream().head_position == 3
                    _run(
                        "exec",
                        "-u",
                        "postgres",
                        primary_container,
                        "createdb",
                        "-U",
                        "postgres",
                        "signal_recipe_restore",
                    )
                    _run(
                        "exec",
                        "-i",
                        "-u",
                        "postgres",
                        primary_container,
                        "pg_restore",
                        "-U",
                        "postgres",
                        "--exit-on-error",
                        "-d",
                        "signal_recipe_restore",
                        input_bytes=recipe_snapshot,
                    )
                    recipe_restored_admin_dsn = make_conninfo(
                        primary_admin_dsn, dbname="signal_recipe_restore"
                    )
                    recipe_restored_dispatcher_dsn = make_conninfo(
                        primary_env["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"],
                        dbname="signal_recipe_restore",
                    )
                    with psycopg.connect(
                        recipe_restored_admin_dsn, autocommit=True
                    ) as recipe_admin:
                        recipe_admin.execute(
                            "GRANT CONNECT ON DATABASE signal_recipe_restore "
                            "TO signal_authority_dispatcher"
                        )
                        assert recipe_admin.execute(
                            "SELECT status FROM control.recipe_release_events "
                            "WHERE release_id=%s ORDER BY sequence_number DESC LIMIT 1",
                            (VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,),
                        ).fetchone() == ("REVIEWED",)
                    with httpx2.Client(
                        base_url=bao.base_url,
                        verify=bao.tls_context,
                        trust_env=False,
                        headers={"X-Vault-Token": bao.root_token, "Accept": "application/json"},
                    ) as root:
                        response = root.post(
                            f"/v1/{AUTHORITY_MOUNT}/data/recovery/current",
                            json={
                                "options": {"cas": 2},
                                "data": {"generation": "after-recipe-restore-" + uuid4().hex},
                            },
                        )
                        assert response.status_code == 200
                    with psycopg.connect(
                        recipe_restored_dispatcher_dsn, autocommit=True
                    ) as recipe_dispatcher:
                        stream = asyncio.run(
                            replay_after_restore(
                                recipe_dispatcher,
                                AuthorityJournal(reader, key.public_key(), encryption_key),
                                recovery_authority,
                                backup_recovery_generation=recipe_backup_generation,
                                verify=bao.tls_context,
                            )
                        )
                        assert stream.head_position == 3
                    with psycopg.connect(
                        recipe_restored_admin_dsn, autocommit=True
                    ) as recipe_admin:
                        assert recipe_admin.execute(
                            "SELECT status,source FROM control.recipe_release_events "
                            "WHERE release_id=%s ORDER BY sequence_number DESC LIMIT 1",
                            (VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,),
                        ).fetchone() == ("REVOKED", "journal_replay")
                        assert recipe_admin.execute(
                            "SELECT restriction_kind FROM control.authority_denial_tombstones "
                            "WHERE event_id=%s",
                            (recipe_event_id,),
                        ).fetchone() == ("recipe_release_revoked",)
                    passed += 1
                    print("PASS real recipe-revocation restore and deny-only replay")

                    standing_event_id = uuid4()
                    current_standing_generation = asyncio.run(
                        recovery_authority.current_generation(verify=bao.tls_context)
                    ).value
                    standing_token = _refresh_standing_session(
                        primary_admin,
                        standing_tenant,
                        standing_site,
                        user_id,
                        current_standing_generation,
                    )
                    with psycopg.connect(
                        primary_env["SIGNAL_TEST_API_DSN"], autocommit=True
                    ) as standing_api:
                        revoked = revoke_standing_authorization(
                            standing_api,
                            session_token=standing_token,
                            current_recovery_generation=current_standing_generation,
                            site_id=standing_site,
                            grant_id=standing_grant.id,
                            event_id=standing_event_id,
                        )
                        assert revoked.durability == "AUTHORITY_DURABILITY_PENDING"
                    assert primary_admin.execute(
                        "SELECT count(*) FROM app.standing_authorization_revocations "
                        "WHERE grant_id=%s",
                        (standing_grant.id,),
                    ).fetchone() == (1,)
                    assert dispatch_pending_restrictions(scheduler, journal) == 1
                    assert scheduler.execute(
                        "SELECT control.authority_durability_status(%s)",
                        (standing_event_id,),
                    ).fetchone() == ("ACKNOWLEDGED",)
                    _run(
                        "exec",
                        "-u",
                        "postgres",
                        primary_container,
                        "createdb",
                        "-U",
                        "postgres",
                        "signal_standing_restore",
                    )
                    _run(
                        "exec",
                        "-i",
                        "-u",
                        "postgres",
                        primary_container,
                        "pg_restore",
                        "-U",
                        "postgres",
                        "--exit-on-error",
                        "-d",
                        "signal_standing_restore",
                        input_bytes=recipe_snapshot,
                    )
                    standing_admin_dsn = make_conninfo(
                        primary_admin_dsn, dbname="signal_standing_restore"
                    )
                    standing_dispatcher_dsn = make_conninfo(
                        primary_env["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"],
                        dbname="signal_standing_restore",
                    )
                    standing_workflow_dsn = make_conninfo(
                        primary_env["SIGNAL_TEST_WORKFLOW_DSN"],
                        dbname="signal_standing_restore",
                    )
                    with psycopg.connect(standing_admin_dsn, autocommit=True) as restored_admin:
                        restored_admin.execute(
                            "GRANT CONNECT ON DATABASE signal_standing_restore "
                            "TO signal_authority_dispatcher, signal_workflow"
                        )
                        assert restored_admin.execute(
                            "SELECT count(*) FROM app.standing_authorization_revocations "
                            "WHERE grant_id=%s",
                            (standing_grant.id,),
                        ).fetchone() == (0,)
                    with psycopg.connect(
                        standing_workflow_dsn, autocommit=True
                    ) as restored_workflow:
                        eligibility_args = (
                            standing_tenant,
                            standing_site,
                            standing_grant.id,
                            standing_generation,
                            VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
                            "draft_patch",
                            "/blog/post",
                        )
                        assert restored_workflow.execute(
                            "SELECT eligible FROM control.standing_grant_eligibility("
                            "%s,%s,%s,%s,%s,%s,%s)",
                            eligibility_args,
                        ).fetchone() == (True,)
                    with httpx2.Client(
                        base_url=bao.base_url,
                        verify=bao.tls_context,
                        trust_env=False,
                        headers={"X-Vault-Token": bao.root_token, "Accept": "application/json"},
                    ) as root:
                        response = root.post(
                            f"/v1/{AUTHORITY_MOUNT}/data/recovery/current",
                            json={
                                "options": {"cas": 3},
                                "data": {"generation": "after-standing-restore-" + uuid4().hex},
                            },
                        )
                        assert response.status_code == 200
                    with psycopg.connect(
                        standing_dispatcher_dsn, autocommit=True
                    ) as restored_dispatcher:
                        stream = asyncio.run(
                            replay_after_restore(
                                restored_dispatcher,
                                AuthorityJournal(reader, key.public_key(), encryption_key),
                                recovery_authority,
                                backup_recovery_generation=standing_generation,
                                verify=bao.tls_context,
                            )
                        )
                        assert stream.head_position == 4
                    with psycopg.connect(standing_admin_dsn, autocommit=True) as restored_admin:
                        assert restored_admin.execute(
                            "SELECT target_kind,restriction_kind "
                            "FROM control.authority_denial_tombstones WHERE event_id=%s",
                            (standing_event_id,),
                        ).fetchone() == ("standing_grant", "standing_grant_revoked")
                        assert restored_admin.execute(
                            "SELECT count(*) FROM app.standing_authorizations "
                            "WHERE id=%s AND tenant_id=%s AND site_id=%s",
                            (standing_grant.id, standing_tenant, standing_site),
                        ).fetchone() == (1,)
                    with psycopg.connect(
                        standing_workflow_dsn, autocommit=True
                    ) as restored_workflow:
                        assert restored_workflow.execute(
                            "SELECT eligible FROM control.standing_grant_eligibility("
                            "%s,%s,%s,%s,%s,%s,%s)",
                            eligibility_args,
                        ).fetchone() == (False,)
                    passed += 1
                    print("PASS real standing-grant revocation restore and deny-only replay")

                    first_entry = journal_admin.execute(
                        "SELECT position,generation,event_id,body,body_hash,writer_signature,"
                        "previous_hash,entry_hash,appended_at FROM authority_journal.entries "
                        "WHERE position=1"
                    ).fetchone()
                    assert first_entry is not None
                    journal_admin.execute(
                        "ALTER TABLE authority_journal.entries DISABLE TRIGGER ALL"
                    )
                    journal_admin.execute("DELETE FROM authority_journal.entries WHERE position=1")
                    assert "Missing journal segment" in str(
                        _expect_raises(AuthorityJournalUnavailable, journal.verify_stream)
                    )
                    journal_admin.execute(
                        "INSERT INTO authority_journal.entries "
                        "(position,generation,event_id,body,body_hash,writer_signature,"
                        "previous_hash,entry_hash,appended_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        first_entry,
                    )
                    journal_admin.execute(
                        "ALTER TABLE authority_journal.entries ENABLE TRIGGER ALL"
                    )
                    assert journal.verify_stream().head_position == 4
                    passed += 1
                    print("PASS missing segment fails closed")
                    original = journal_admin.execute(
                        "SELECT writer_signature,entry_hash FROM authority_journal.entries "
                        "WHERE position=4"
                    ).fetchone()
                    assert original is not None
                    journal_admin.execute(
                        "ALTER TABLE authority_journal.entries DISABLE TRIGGER ALL"
                    )
                    journal_admin.execute(
                        "UPDATE authority_journal.entries SET writer_signature=%s, "
                        "entry_hash=public.digest(previous_hash || int8send(position) "
                        "|| body_hash || %s, 'sha256') WHERE position=4",
                        (bytes(64), bytes(64)),
                    )
                    tampered_hash = journal_admin.execute(
                        "SELECT entry_hash FROM authority_journal.entries WHERE position=4"
                    ).fetchone()[0]
                    journal_admin.execute(
                        "UPDATE authority_journal.stream SET head_hash=%s", (tampered_hash,)
                    )
                    assert "Invalid journal signature" in str(
                        _expect_raises(AuthorityJournalUnavailable, journal.verify_stream)
                    )
                    journal_admin.execute(
                        "UPDATE authority_journal.entries SET writer_signature=%s,entry_hash=%s "
                        "WHERE position=4",
                        original,
                    )
                    journal_admin.execute(
                        "UPDATE authority_journal.stream SET head_hash=%s",
                        (original[1],),
                    )
                    journal_admin.execute(
                        "ALTER TABLE authority_journal.entries ENABLE TRIGGER ALL"
                    )
                    assert journal.verify_stream().head_position == 4
                    passed += 1
                    print("PASS invalid writer signature fails closed")
                    connector_targets = [
                        (uuid4(), kind)
                        for kind in (
                            "slack_binding",
                            "slack_link",
                            "ga4_binding",
                            "telegram_binding",
                            "telegram_link",
                            "wordpress_binding",
                            "github_pr_extension",
                        )
                    ]
                    for target, kind in connector_targets:
                        journal.append(
                            RestrictionRecord(
                                uuid4(),
                                user_id,
                                target,
                                1,
                                datetime.now(UTC),
                                "a" * 64,
                                kind,
                                kind + "_revoked",
                            )
                        )
                    with psycopg.connect(restored_scheduler_dsn, autocommit=True) as restored:
                        asyncio.run(
                            replay_after_restore(
                                restored,
                                journal,
                                recovery_authority,
                                backup_recovery_generation=backup_generation,
                                verify=bao.tls_context,
                            )
                        )
                    with psycopg.connect(restored_admin_dsn, autocommit=True) as restored_admin:
                        for target, kind in connector_targets:
                            assert restored_admin.execute(
                                "SELECT target_kind FROM control.authority_denial_tombstones "
                                "WHERE target_id=%s",
                                (target,),
                            ).fetchone() == (kind,)
                    passed += 1
                    print(
                        "PASS independent Slack/Telegram/WordPress journal "
                        "and restored-primary deny-only replay"
                    )
                    passed += 1
                    print("PASS independent GA4 journal and restored-primary deny-only replay")
                    passed += 1
                    print(
                        "PASS independent GitHub PR restriction journal "
                        "and restored-primary deny-only replay"
                    )
                    docs_target = uuid4()
                    journal.append(
                        RestrictionRecord(
                            uuid4(),
                            user_id,
                            docs_target,
                            1,
                            datetime.now(UTC),
                            "b" * 64,
                            "docs_binding",
                            "docs_binding_revoked",
                        )
                    )
                    with psycopg.connect(restored_scheduler_dsn, autocommit=True) as restored:
                        asyncio.run(
                            replay_after_restore(
                                restored,
                                journal,
                                recovery_authority,
                                backup_recovery_generation=backup_generation,
                                verify=bao.tls_context,
                            )
                        )
                    with psycopg.connect(restored_admin_dsn, autocommit=True) as restored_admin:
                        assert restored_admin.execute(
                            "SELECT target_kind,restriction_kind "
                            "FROM control.authority_denial_tombstones WHERE target_id=%s",
                            (docs_target,),
                        ).fetchone() == ("docs_binding", "docs_binding_revoked")
                    passed += 1
                    print(
                        "PASS independent Google Docs journal and restored-primary deny-only replay"
                    )
                    passed += _qualify_invitation_revocation(
                        primary_admin_dsn,
                        primary_env,
                        primary_container,
                        primary_admin,
                        identity,
                        scheduler,
                        journal,
                        unavailable_journal,
                        reader,
                        key,
                        encryption_key,
                        recovery_authority,
                        bao,
                        user_id,
                        standing_tenant,
                        standing_site,
                    )
                    journal_admin.execute("DELETE FROM authority_journal.stream")
                    assert "head unavailable" in str(
                        _expect_raises(AuthorityJournalUnavailable, journal.verify_stream)
                    )
                    passed += 1
                    print("PASS unavailable stream head fails closed")
    print(f"Authority journal lab: {passed} passed, 0 failed.")
    return 0
