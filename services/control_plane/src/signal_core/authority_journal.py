"""Deny-only authority journal on a separately operated PostgreSQL cluster."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import rfc8785
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from psycopg import Connection, Error

from signal_core.recovery_authority import OpenBaoRecoveryAuthority, RecoveryGeneration

_ZERO = bytes(32)
_MAX_ENTRIES = 1_000_000
_DIGEST = re.compile(r"[0-9a-f]{64}")


class AuthorityJournalUnavailable(Exception):
    """Independent journal is unavailable or its authenticated history is incomplete."""


class AuthorityJournalConflict(Exception):
    """One stable event ID was presented with a different signed body."""


@dataclass(frozen=True)
class RestrictionRecord:
    event_id: UUID
    actor_user_id: UUID
    target_id: UUID
    effective_epoch: int
    event_time: datetime
    payload_hash: str
    target_kind: str = "identity_session"
    restriction_kind: str = "session_revoked"

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, UUID)
                for value in (self.event_id, self.actor_user_id, self.target_id)
            )
            or type(self.effective_epoch) is not int
            or self.effective_epoch < 1
            or not isinstance(self.payload_hash, str)
            or _DIGEST.fullmatch(self.payload_hash) is None
            or (self.target_kind, self.restriction_kind)
            not in {
                ("identity_session", "session_revoked"),
                ("recipe_release", "recipe_release_revoked"),
                ("standing_grant", "standing_grant_revoked"),
                ("slack_binding", "slack_binding_revoked"),
                ("slack_link", "slack_link_revoked"),
                ("ga4_binding", "ga4_binding_revoked"),
                ("telegram_binding", "telegram_binding_revoked"),
                ("telegram_link", "telegram_link_revoked"),
                ("wordpress_binding", "wordpress_binding_revoked"),
                ("docs_binding", "docs_binding_revoked"),
                ("webflow_binding", "webflow_binding_revoked"),
                ("github_pr_extension", "github_pr_extension_revoked"),
                ("invitation", "invitation_revoked"),
            }
        ):
            raise ValueError("Invalid restriction record.")
        if not isinstance(self.event_time, datetime) or self.event_time.tzinfo is None:
            raise ValueError("Restriction event time must be timezone-aware.")

    def canonical_body(self) -> bytes:
        return rfc8785.dumps(
            {
                "schema_version": 1,
                "event_id": str(self.event_id),
                "scope": {"kind": "platform"},
                "actor": {"kind": "user", "id": str(self.actor_user_id)},
                "target": {"kind": self.target_kind, "id": str(self.target_id)},
                "restriction_kind": self.restriction_kind,
                "effective_epoch": self.effective_epoch,
                "event_time": self.event_time.astimezone(UTC).isoformat(),
                "payload_hash": self.payload_hash,
            }
        )

    @classmethod
    def from_body(cls, body: bytes) -> "RestrictionRecord":
        try:
            document = json.loads(body)
            if rfc8785.dumps(document) != body or set(document) != {
                "schema_version",
                "event_id",
                "scope",
                "actor",
                "target",
                "restriction_kind",
                "effective_epoch",
                "event_time",
                "payload_hash",
            }:
                raise ValueError
            if (
                document["schema_version"] != 1
                or document["scope"] != {"kind": "platform"}
                or document["actor"].get("kind") != "user"
                or set(document["actor"]) != {"kind", "id"}
                or document["target"].get("kind")
                not in {
                    "identity_session",
                    "recipe_release",
                    "standing_grant",
                    "slack_binding",
                    "webflow_binding",
                    "github_pr_extension",
                    "slack_link",
                    "ga4_binding",
                    "telegram_binding",
                    "telegram_link",
                    "wordpress_binding",
                    "docs_binding",
                    "invitation",
                }
                or set(document["target"]) != {"kind", "id"}
                or type(document["effective_epoch"]) is not int
            ):
                raise ValueError
            record = cls(
                event_id=UUID(document["event_id"]),
                actor_user_id=UUID(document["actor"]["id"]),
                target_id=UUID(document["target"]["id"]),
                effective_epoch=document["effective_epoch"],
                event_time=datetime.fromisoformat(document["event_time"]),
                payload_hash=document["payload_hash"],
                target_kind=document["target"]["kind"],
                restriction_kind=document["restriction_kind"],
            )
            if record.canonical_body() != body:
                raise ValueError
            return record
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AuthorityJournalUnavailable("Invalid journal record.") from None


@dataclass(frozen=True)
class JournalReceipt:
    generation: UUID
    position: int
    event_id: UUID
    body_hash: str
    verified_at: datetime


@dataclass(frozen=True)
class VerifiedStream:
    generation: UUID
    head_position: int
    head_hash: str
    entries: tuple[tuple[RestrictionRecord, JournalReceipt], ...]


class AuthorityJournal:
    """Only the writer can append; reader verifies the complete independent head."""

    def __init__(
        self,
        connection: Connection,
        verify_key: Ed25519PublicKey,
        encryption_key: bytes,
        signing_key: Ed25519PrivateKey | None = None,
    ) -> None:
        if not isinstance(encryption_key, bytes) or len(encryption_key) != 64:
            raise ValueError("Journal encryption key is invalid.")
        if not connection.autocommit:
            raise ValueError("Journal connection must be autocommit.")
        self.connection = connection
        self.verify_key = verify_key
        self.cipher = AESSIV(encryption_key)
        self.signing_key = signing_key

    def verify_stream(self) -> VerifiedStream:
        try:
            with self.connection.transaction():
                head_rows = self.connection.execute(
                    "SELECT generation, head_position, head_hash FROM authority_journal.stream"
                ).fetchall()
                if len(head_rows) != 1:
                    raise AuthorityJournalUnavailable("Journal stream head unavailable.")
                generation, position, head_hash = head_rows[0]
                if not isinstance(generation, UUID) or not 0 <= position <= _MAX_ENTRIES:
                    raise AuthorityJournalUnavailable("Invalid journal stream head.")
                rows = self.connection.execute(
                    "SELECT position, generation, event_id, body, body_hash, writer_signature, "
                    "previous_hash, entry_hash FROM authority_journal.entries ORDER BY position"
                ).fetchall()
                if len(rows) != position:
                    raise AuthorityJournalUnavailable("Missing journal segment.")
                previous = _ZERO
                entries = []
                seen = set()
                for expected, row in enumerate(rows, 1):
                    (
                        number,
                        row_generation,
                        event_id,
                        body,
                        body_hash,
                        signature,
                        previous_hash,
                        entry_hash,
                    ) = row
                    ciphertext = bytes(body)
                    body_hash = bytes(body_hash)
                    signature = bytes(signature)
                    if (
                        number != expected
                        or row_generation != generation
                        or event_id in seen
                        or previous_hash != previous
                        or sha256(ciphertext).digest() != body_hash
                        or sha256(
                            previous
                            + number.to_bytes(8, "big", signed=True)
                            + body_hash
                            + signature
                        ).digest()
                        != entry_hash
                    ):
                        raise AuthorityJournalUnavailable("Journal continuity failure.")
                    seen.add(event_id)
                    try:
                        body = self.cipher.decrypt(ciphertext, [event_id.bytes, signature])
                        self.verify_key.verify(signature, body)
                    except (InvalidSignature, InvalidTag, ValueError):
                        raise AuthorityJournalUnavailable("Invalid journal signature.") from None
                    record = RestrictionRecord.from_body(body)
                    if record.event_id != event_id:
                        raise AuthorityJournalUnavailable("Journal event identity mismatch.")
                    entries.append(
                        (
                            record,
                            JournalReceipt(
                                generation=generation,
                                position=number,
                                event_id=event_id,
                                body_hash=body_hash.hex(),
                                verified_at=datetime.now(UTC),
                            ),
                        )
                    )
                    previous = bytes(entry_hash)
                if previous != head_hash:
                    raise AuthorityJournalUnavailable("Journal stream head mismatch.")
                return VerifiedStream(generation, position, previous.hex(), tuple(entries))
        except Error:
            raise AuthorityJournalUnavailable("Journal stream unavailable.") from None

    def append(self, record: RestrictionRecord) -> JournalReceipt:
        if self.signing_key is None:
            raise AuthorityJournalUnavailable("Journal signer unavailable.")
        body = record.canonical_body()
        signature = self.signing_key.sign(body)
        ciphertext = self.cipher.encrypt(body, [record.event_id.bytes, signature])
        try:
            with self.connection.transaction():
                row = self.connection.execute(
                    "SELECT generation, stream_position, body_hash "
                    "FROM authority_journal.append(%s, %s, %s)",
                    (record.event_id, ciphertext, signature),
                ).fetchone()
            if row is None:
                raise AuthorityJournalUnavailable("Journal acknowledgement unavailable.")
        except Error as error:
            if error.sqlstate == "23505":
                raise AuthorityJournalConflict("Journal event identity conflict.") from None
            raise AuthorityJournalUnavailable("Journal append unavailable.") from None
        stream = self.verify_stream()
        for stored_record, receipt in stream.entries:
            if stored_record.event_id == record.event_id:
                if (
                    receipt.generation != row[0]
                    or receipt.position != row[1]
                    or bytes(row[2]).hex() != receipt.body_hash
                    or stored_record.canonical_body() != body
                ):
                    raise AuthorityJournalUnavailable("Journal acknowledgement mismatch.")
                return receipt
        raise AuthorityJournalUnavailable("Journal acknowledgement missing.")


def dispatch_pending_restrictions(primary: Connection, journal: AuthorityJournal) -> int:
    """Retry exact local events; never undo an effective local restriction on failure."""
    if not primary.autocommit:
        raise ValueError("Authority dispatcher connection must be autocommit.")
    with primary.transaction():
        rows = primary.execute(
            "SELECT * FROM control.pending_authority_restrictions(100)"
        ).fetchall()
    completed = 0
    for event_id, actor_id, target_id, epoch, event_time, facts in rows:
        payload_hash = sha256(rfc8785.dumps(facts)).hexdigest()
        restriction_kind = facts.get("restriction_kind", "session_revoked")
        target_kind = {
            "docs_binding_revoked": "docs_binding",
            "session_revoked": "identity_session",
            "recipe_release_revoked": "recipe_release",
            "standing_grant_revoked": "standing_grant",
            "slack_binding_revoked": "slack_binding",
            "slack_link_revoked": "slack_link",
            "ga4_binding_revoked": "ga4_binding",
            "telegram_binding_revoked": "telegram_binding",
            "telegram_link_revoked": "telegram_link",
            "wordpress_binding_revoked": "wordpress_binding",
            "webflow_binding_revoked": "webflow_binding",
            "github_pr_extension_revoked": "github_pr_extension",
            "invitation_revoked": "invitation",
        }.get(restriction_kind)
        if target_kind is None:
            raise AuthorityJournalUnavailable("Unknown restriction kind.")
        receipt = journal.append(
            RestrictionRecord(
                event_id,
                actor_id,
                target_id,
                epoch,
                event_time,
                payload_hash,
                target_kind,
                restriction_kind,
            )
        )
        with primary.transaction():
            primary.execute(
                "SELECT control.record_authority_restriction_receipt(%s,%s,%s,%s,%s,%s)",
                (
                    uuid4(),
                    event_id,
                    receipt.generation,
                    receipt.position,
                    receipt.body_hash,
                    receipt.verified_at,
                ),
            )
        completed += 1
    return completed


async def replay_after_restore(
    primary: Connection,
    journal: AuthorityJournal,
    recovery_authority: OpenBaoRecoveryAuthority,
    *,
    backup_recovery_generation: str,
    **authority_options: object,
) -> VerifiedStream:
    """Fetch fresh external generation, verify all journal bytes, then deny-only replay."""
    if not primary.autocommit:
        raise ValueError("Authority replay connection must be autocommit.")
    current: RecoveryGeneration = await recovery_authority.current_generation(**authority_options)
    if current.value == backup_recovery_generation:
        raise AuthorityJournalUnavailable("Recovery generation was not rotated.")
    stream = journal.verify_stream()
    try:
        with primary.transaction():
            for record, receipt in stream.entries:
                if record.target_kind in {
                    "slack_binding",
                    "slack_link",
                    "telegram_binding",
                    "telegram_link",
                }:
                    provider = "telegram" if record.target_kind.startswith("telegram_") else "slack"
                    primary.execute(
                        f"SELECT control.apply_{provider}_authority_denial(%s,%s,%s,%s,%s,%s,%s)",
                        (
                            record.event_id,
                            record.target_id,
                            record.effective_epoch,
                            receipt.generation,
                            receipt.position,
                            receipt.body_hash,
                            record.target_kind,
                        ),
                    )
                    continue
                apply_function = {
                    "docs_binding": "control.apply_docs_binding_denial",
                    "identity_session": "control.apply_authority_denial",
                    "recipe_release": "control.apply_recipe_release_denial",
                    "standing_grant": "control.apply_standing_grant_denial",
                    "ga4_binding": "control.apply_ga4_binding_denial",
                    "wordpress_binding": "control.apply_wordpress_authority_denial",
                    "webflow_binding": "control.apply_webflow_authority_denial",
                    "github_pr_extension": "control.apply_github_pr_authority_denial",
                    "invitation": "control.apply_invitation_authority_denial",
                }[record.target_kind]
                primary.execute(
                    f"SELECT {apply_function}(%s,%s,%s,%s,%s,%s)",
                    (
                        record.event_id,
                        record.target_id,
                        record.effective_epoch,
                        receipt.generation,
                        receipt.position,
                        receipt.body_hash,
                    ),
                )
            primary.execute(
                "SELECT control.record_authority_replay_checkpoint(%s,%s,%s,%s,%s)",
                (uuid4(), stream.generation, stream.head_position, stream.head_hash, current.value),
            )
    except Error:
        raise AuthorityJournalUnavailable("Authority replay failed.") from None
    return stream
