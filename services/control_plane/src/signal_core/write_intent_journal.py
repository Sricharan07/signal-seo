"""Independent, authenticated journal for possible external write effects."""

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

import rfc8785
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from psycopg import Connection, Error

from signal_core.authority_journal import JournalReceipt
from signal_core.indexnow_protocol import INDEXNOW_ENDPOINT

_SHA1 = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class WriteIntentJournalUnavailable(Exception):
    """The independent stream cannot provide a verified durable acknowledgement."""


class WriteIntentJournalConflict(Exception):
    """A stable operation was presented with different write intent."""


@dataclass(frozen=True)
class WebflowWriteIntentRecord:
    operation_id: UUID
    site_id: UUID
    binding_id: UUID
    provider_site: str
    collection_id: str
    revision_sha256: str
    slug: str

    def canonical_body(self) -> bytes:
        if (
            any(not isinstance(v, UUID) for v in (self.operation_id, self.site_id, self.binding_id))
            or any(
                not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{24}", v) is None
                for v in (self.provider_site, self.collection_id)
            )
            or not isinstance(self.revision_sha256, str)
            or _SHA256.fullmatch(self.revision_sha256) is None
            or not isinstance(self.slug, str)
            or re.fullmatch(r"[a-z0-9-]{1,80}-signal-" + self.operation_id.hex, self.slug) is None
        ):
            raise ValueError("Invalid Webflow write intent.")
        return rfc8785.dumps(
            {
                "schema_version": 1,
                "provider": "webflow",
                "effect": "create_primary_locale_draft_once",
                "operation_id": str(self.operation_id),
                "site_id": str(self.site_id),
                "binding_id": str(self.binding_id),
                "provider_site": self.provider_site,
                "collection_id": self.collection_id,
                "revision_sha256": self.revision_sha256,
                "slug": self.slug,
            }
        )

    @classmethod
    def from_body(cls, body: bytes) -> "WebflowWriteIntentRecord":
        try:
            value = json.loads(body)
            record = cls(
                UUID(value["operation_id"]),
                UUID(value["site_id"]),
                UUID(value["binding_id"]),
                value["provider_site"],
                value["collection_id"],
                value["revision_sha256"],
                value["slug"],
            )
            if record.canonical_body() != body:
                raise ValueError
            return record
        except (KeyError, TypeError, ValueError):
            raise WriteIntentJournalUnavailable("Invalid Webflow write intent record.") from None


@dataclass(frozen=True)
class IndexNowWriteIntentRecord:
    operation_id: UUID
    site_id: UUID
    change_id: UUID
    delivery_receipt_sha256: str
    key_sha256: str
    url: str
    intent_sha256: str

    def canonical_body(self) -> bytes:
        from signal_core.crawl_urls import normalize_crawl_url

        if (
            not all(
                isinstance(value, UUID)
                for value in (self.operation_id, self.site_id, self.change_id)
            )
            or any(
                not isinstance(value, str) or _SHA256.fullmatch(value) is None
                for value in (self.delivery_receipt_sha256, self.key_sha256, self.intent_sha256)
            )
            or normalize_crawl_url(self.url).fetch_url != self.url
            or len(self.url) > 2048
        ):
            raise ValueError("Invalid IndexNow write intent.")
        return rfc8785.dumps(
            {
                "schema_version": 1,
                "kind": "indexnow_submit",
                "endpoint": INDEXNOW_ENDPOINT,
                "operation_id": str(self.operation_id),
                "site_id": str(self.site_id),
                "change_id": str(self.change_id),
                "delivery_receipt_sha256": self.delivery_receipt_sha256,
                "key_sha256": self.key_sha256,
                "url": self.url,
                "intent_sha256": self.intent_sha256,
            }
        )

    @classmethod
    def from_body(cls, body: bytes) -> "IndexNowWriteIntentRecord":
        try:
            value = json.loads(body)
            record = cls(
                UUID(value["operation_id"]),
                UUID(value["site_id"]),
                UUID(value["change_id"]),
                value["delivery_receipt_sha256"],
                value["key_sha256"],
                value["url"],
                value["intent_sha256"],
            )
            if record.canonical_body() != body:
                raise ValueError
            return record
        except (KeyError, TypeError, ValueError):
            raise WriteIntentJournalUnavailable("Invalid IndexNow write intent.") from None


@dataclass(frozen=True)
class WriteIntentRecord:
    operation_id: UUID
    site_id: UUID
    repository_id: int
    revision_sha256: str
    intent_sha256: str
    base_sha: str
    patch_sha256: str
    branch_name: str
    recovery_plan_sha256: str

    def canonical_body(self) -> bytes:
        if (
            not isinstance(self.operation_id, UUID)
            or not isinstance(self.site_id, UUID)
            or type(self.repository_id) is not int
            or self.repository_id < 1
            or any(
                not isinstance(value, str) or _SHA256.fullmatch(value) is None
                for value in (
                    self.revision_sha256,
                    self.intent_sha256,
                    self.patch_sha256,
                    self.recovery_plan_sha256,
                )
            )
            or any(
                not isinstance(value, str) or _SHA1.fullmatch(value) is None
                for value in (self.base_sha,)
            )
            or self.branch_name != f"signal/{self.operation_id.hex}"
        ):
            raise ValueError("Invalid GitHub write intent.")
        return rfc8785.dumps(
            {
                "schema_version": 1,
                "operation_id": str(self.operation_id),
                "site_id": str(self.site_id),
                "repository_id": self.repository_id,
                "revision_sha256": self.revision_sha256,
                "intent_sha256": self.intent_sha256,
                "base_sha": self.base_sha,
                "patch_sha256": self.patch_sha256,
                "branch_name": self.branch_name,
                "recovery_plan_sha256": self.recovery_plan_sha256,
            }
        )

    @classmethod
    def from_body(cls, body: bytes) -> "WriteIntentRecord":
        try:
            value = json.loads(body)
            if (
                set(value)
                != {
                    "schema_version",
                    "operation_id",
                    "site_id",
                    "repository_id",
                    "revision_sha256",
                    "intent_sha256",
                    "base_sha",
                    "patch_sha256",
                    "branch_name",
                    "recovery_plan_sha256",
                }
                or value["schema_version"] != 1
            ):
                raise ValueError
            record = cls(
                UUID(value["operation_id"]),
                UUID(value["site_id"]),
                value["repository_id"],
                value["revision_sha256"],
                value["intent_sha256"],
                value["base_sha"],
                value["patch_sha256"],
                value["branch_name"],
                value["recovery_plan_sha256"],
            )
            if record.canonical_body() != body:
                raise ValueError
            return record
        except (KeyError, TypeError, ValueError):
            raise WriteIntentJournalUnavailable("Invalid write intent record.") from None


@dataclass(frozen=True)
class WordPressWriteIntentRecord:
    operation_id: UUID
    intent_id: UUID
    attempt: int
    site_id: UUID
    binding_id: UUID
    revision_sha256: str
    intent_sha256: str
    slug: str
    recovery_generation: str

    def canonical_body(self):
        if (
            not all(
                isinstance(v, UUID)
                for v in (self.operation_id, self.intent_id, self.site_id, self.binding_id)
            )
            or type(self.attempt) is not int
            or self.attempt not in {1, 2}
            or self.operation_id
            != uuid5(NAMESPACE_URL, f"signal.wordpress.dispatch:{self.intent_id}:{self.attempt}")
            or any(
                not isinstance(v, str) or _SHA256.fullmatch(v) is None
                for v in (self.revision_sha256, self.intent_sha256)
            )
            or self.slug != "signal-s" + self.intent_id.hex
            or not isinstance(self.recovery_generation, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.recovery_generation) is None
        ):
            raise ValueError("Invalid WordPress write intent.")
        return rfc8785.dumps(
            {
                "schema_version": 1,
                "provider": "wordpress_draft",
                "operation_id": str(self.operation_id),
                "intent_id": str(self.intent_id),
                "attempt": self.attempt,
                "site_id": str(self.site_id),
                "binding_id": str(self.binding_id),
                "revision_sha256": self.revision_sha256,
                "intent_sha256": self.intent_sha256,
                "slug": self.slug,
                "recovery_generation": self.recovery_generation,
            }
        )

    @classmethod
    def from_body(cls, body):
        try:
            value = json.loads(body)
            record = cls(
                UUID(value["operation_id"]),
                UUID(value["intent_id"]),
                value["attempt"],
                UUID(value["site_id"]),
                UUID(value["binding_id"]),
                value["revision_sha256"],
                value["intent_sha256"],
                value["slug"],
                value["recovery_generation"],
            )
            if record.canonical_body() != body:
                raise ValueError
            return record
        except (KeyError, TypeError, ValueError):
            raise WriteIntentJournalUnavailable("Invalid WordPress write intent record.") from None


class WriteIntentJournal:
    """Append exact intents on a separate cluster and verify the complete hash chain."""

    def __init__(
        self,
        connection: Connection,
        verify_key: Ed25519PublicKey,
        encryption_key: bytes,
        signing_key: Ed25519PrivateKey | None = None,
    ) -> None:
        if (
            not connection.autocommit
            or not isinstance(encryption_key, bytes)
            or len(encryption_key) != 64
        ):
            raise ValueError("Write journal configuration is invalid.")
        self.connection = connection
        self.verify_key = verify_key
        self.cipher = AESSIV(encryption_key)
        self.signing_key = signing_key

    def verify_stream(
        self,
    ) -> tuple[
        UUID,
        tuple[
            tuple[
                WriteIntentRecord
                | IndexNowWriteIntentRecord
                | WordPressWriteIntentRecord
                | WebflowWriteIntentRecord,
                JournalReceipt,
            ],
            ...,
        ],
    ]:
        try:
            with self.connection.transaction():
                head = self.connection.execute(
                    "SELECT generation, head_position, head_hash FROM write_journal.stream"
                ).fetchone()
                if head is None or not isinstance(head[0], UUID) or not 0 <= head[1] <= 1_000_000:
                    raise WriteIntentJournalUnavailable("Write journal head unavailable.")
                rows = self.connection.execute(
                    "SELECT position,generation,operation_id,body,body_hash,writer_signature,"
                    "previous_hash,entry_hash FROM write_journal.entries ORDER BY position"
                ).fetchall()
                if len(rows) != head[1]:
                    raise WriteIntentJournalUnavailable("Missing write journal segment.")
                previous = bytes(32)
                entries = []
                seen = set()
                for position, row in enumerate(rows, 1):
                    (
                        number,
                        generation,
                        operation_id,
                        encrypted,
                        body_hash,
                        signature,
                        prior,
                        entry_hash,
                    ) = row
                    encrypted, body_hash, signature = (
                        bytes(encrypted),
                        bytes(body_hash),
                        bytes(signature),
                    )
                    if (
                        number != position
                        or generation != head[0]
                        or operation_id in seen
                        or bytes(prior) != previous
                        or sha256(encrypted).digest() != body_hash
                        or sha256(
                            previous
                            + position.to_bytes(8, "big", signed=True)
                            + body_hash
                            + signature
                        ).digest()
                        != bytes(entry_hash)
                    ):
                        raise WriteIntentJournalUnavailable("Write journal continuity failure.")
                    seen.add(operation_id)
                    try:
                        body = self.cipher.decrypt(encrypted, [operation_id.bytes, signature])
                        self.verify_key.verify(signature, body)
                    except (InvalidSignature, InvalidTag, ValueError):
                        raise WriteIntentJournalUnavailable(
                            "Write journal signature failure."
                        ) from None
                    try:
                        document = json.loads(body)
                        if not isinstance(document, dict):
                            raise ValueError
                        record = (
                            IndexNowWriteIntentRecord.from_body(body)
                            if document.get("kind") == "indexnow_submit"
                            else WordPressWriteIntentRecord.from_body(body)
                            if document.get("provider") == "wordpress_draft"
                            else WebflowWriteIntentRecord.from_body(body)
                            if document.get("provider") == "webflow"
                            else WriteIntentRecord.from_body(body)
                        )
                    except (ValueError, UnicodeError):
                        raise WriteIntentJournalUnavailable(
                            "Invalid write intent record."
                        ) from None
                    if record.operation_id != operation_id:
                        raise WriteIntentJournalUnavailable("Write journal identity mismatch.")
                    from datetime import UTC, datetime

                    entries.append(
                        (
                            record,
                            JournalReceipt(
                                generation,
                                position,
                                operation_id,
                                body_hash.hex(),
                                datetime.now(UTC),
                            ),
                        )
                    )
                    previous = bytes(entry_hash)
                if previous != bytes(head[2]):
                    raise WriteIntentJournalUnavailable("Write journal head mismatch.")
                return head[0], tuple(entries)
        except Error:
            raise WriteIntentJournalUnavailable("Write journal unavailable.") from None

    def append(
        self,
        record: WriteIntentRecord
        | IndexNowWriteIntentRecord
        | WordPressWriteIntentRecord
        | WebflowWriteIntentRecord,
    ) -> JournalReceipt:
        if self.signing_key is None:
            raise WriteIntentJournalUnavailable("Write journal signer unavailable.")
        body = record.canonical_body()
        signature = self.signing_key.sign(body)
        encrypted = self.cipher.encrypt(body, [record.operation_id.bytes, signature])
        try:
            with self.connection.transaction():
                row = self.connection.execute(
                    "SELECT generation,stream_position,body_hash "
                    "FROM write_journal.append(%s,%s,%s)",
                    (record.operation_id, encrypted, signature),
                ).fetchone()
        except Error as error:
            if error.sqlstate == "23505":
                raise WriteIntentJournalConflict("Write intent identity conflict.") from None
            raise WriteIntentJournalUnavailable("Write journal append unavailable.") from None
        if row is None:
            raise WriteIntentJournalUnavailable("Write journal acknowledgement unavailable.")
        _, entries = self.verify_stream()
        for stored, receipt in entries:
            if stored.operation_id == record.operation_id:
                if (
                    stored != record
                    or receipt.generation != row[0]
                    or receipt.position != row[1]
                    or receipt.body_hash != bytes(row[2]).hex()
                ):
                    raise WriteIntentJournalUnavailable("Write journal acknowledgement mismatch.")
                return receipt
        raise WriteIntentJournalUnavailable("Write journal acknowledgement missing.")
