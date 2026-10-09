"""Encrypted crawl artifacts and append-only fetch-observation persistence."""

import base64
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import stat
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4, uuid5

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from signal_core.crawl_frontier import CrawlFrontierLease
from signal_core.crawl_http import CrawlFetchResult
from signal_core.crawl_urls import validate_public_addresses
from signal_core.database import Scope, _clean_transaction


class ArtifactUnavailable(RuntimeError):
    """An exact encrypted object or registered artifact cannot be read."""


class ArtifactMissing(ArtifactUnavailable):
    """The exact immutable object is absent from its configured backend."""


class ArtifactIntegrityError(RuntimeError):
    """Stored bytes do not authenticate as the registered immutable artifact."""


class ArtifactConflict(RuntimeError):
    """An immutable artifact identity is already bound to different evidence."""


class FetchObservationUnavailable(RuntimeError):
    """The exact crawl lease cannot accept a fetch observation."""


class FetchObservationConflict(RuntimeError):
    """The fetch attempt is already bound to different immutable evidence."""


@dataclass(frozen=True, repr=False)
class ArtifactEncryptionKey:
    reference: str
    material: bytes

    def __post_init__(self) -> None:
        if (
            not isinstance(self.reference, str)
            or _KEY_REFERENCE.fullmatch(self.reference) is None
            or ".." in self.reference
            or "//" in self.reference
            or not isinstance(self.material, bytes)
            or len(self.material) != 32
        ):
            raise ValueError("A valid 256-bit artifact encryption key is required.")


@dataclass(frozen=True, repr=False)
class ArtifactObject:
    tenant_id: UUID
    site_id: UUID
    artifact_id: UUID
    object_key: str
    object_version: str
    sha256: str
    byte_length: int
    media_type: str
    encryption_key_ref: str
    created_at: datetime
    duplicate: bool


@dataclass(frozen=True, repr=False)
class ArtifactRecord:
    tenant_id: UUID
    site_id: UUID
    artifact_id: UUID
    object_key: str
    object_version: str
    sha256: str
    byte_length: int
    media_type: str
    encryption_key_ref: str
    created_at: datetime
    retain_until: datetime
    legal_hold: bool
    durability_state: str


@dataclass(frozen=True, repr=False)
class FetchObservationRecorded:
    observation_id: UUID
    tenant_id: UUID
    site_id: UUID
    crawl_run_id: UUID
    frontier_id: UUID
    url_id: UUID
    fetch_attempt_id: UUID
    started_at: datetime
    finished_at: datetime
    outcome: str
    http_status: int
    final_url: str
    response_headers: tuple[tuple[str, str], ...]
    redirect_chain: tuple[str, ...]
    resolved_address: str
    media_type: str | None
    decoded_bytes: int
    elapsed_ms: int
    network_profile_sha256: str
    raw_artifact: ArtifactRecord | None
    duplicate: bool


@dataclass(frozen=True, repr=False)
class ArtifactAttestationRecorded:
    attestation_id: UUID
    artifact_id: UUID
    check_type: str
    result: str
    verified_sha256: str | None
    verified_at: datetime
    durability_state: str
    duplicate: bool


@dataclass(frozen=True)
class OrphanCleanupResult:
    scanned: int
    deleted: int
    registered: int
    recent: int
    unreadable: int


class EncryptedLocalArtifactStore:
    """A private, no-overwrite AES-GCM object backend rooted at one directory."""

    def __init__(self, root: Path, *, max_plaintext_bytes: int = 64 * 1024 * 1024) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("Artifact storage requires an absolute pathlib root.")
        if not _integer_between(max_plaintext_bytes, 1024, 1024**3):
            raise ValueError("Artifact storage size limit is invalid.")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.resolve(strict=True) != root:
            raise ArtifactUnavailable("Artifact storage root cannot contain symbolic links.")
        self._root = root
        self._max_plaintext_bytes = max_plaintext_bytes
        self._validate_directory(root)

    @property
    def root(self) -> Path:
        return self._root

    def put(
        self,
        scope: Scope,
        artifact_id: UUID,
        plaintext: bytes,
        *,
        media_type: str,
        key: ArtifactEncryptionKey,
        created_at: datetime | None = None,
    ) -> ArtifactObject:
        _validate_store_input(scope, artifact_id, plaintext, media_type, key)
        if len(plaintext) > self._max_plaintext_bytes:
            raise ValueError("Artifact plaintext exceeds the configured limit.")
        observed_at = _utc(created_at or datetime.now(UTC), "artifact creation time")
        digest = hashlib.sha256(plaintext).hexdigest()
        with self._artifact_lock(scope, artifact_id):
            return self._put_locked(
                scope,
                artifact_id,
                plaintext,
                digest=digest,
                media_type=media_type,
                key=key,
                created_at=observed_at,
            )

    def read(
        self,
        artifact: ArtifactObject | ArtifactRecord,
        *,
        key: ArtifactEncryptionKey,
    ) -> bytes:
        _validate_artifact_handle(artifact)
        if not isinstance(key, ArtifactEncryptionKey):
            raise ValueError("A validated artifact encryption key is required.")
        if key.reference != artifact.encryption_key_ref:
            raise ArtifactUnavailable("The requested artifact key reference is unavailable.")
        if isinstance(artifact, ArtifactRecord) and artifact.durability_state != "verified":
            raise ArtifactUnavailable("Artifact use requires a verified durability state.")
        scope = Scope(artifact.tenant_id, artifact.site_id)
        with self._artifact_lock(scope, artifact.artifact_id):
            return self._read_locked(artifact, key=key)

    @contextmanager
    def stage_verified(
        self,
        scope: Scope,
        artifact_id: UUID,
        plaintext: bytes,
        *,
        media_type: str,
        key: ArtifactEncryptionKey,
        created_at: datetime | None = None,
    ) -> Iterator[ArtifactObject]:
        """Hold the object lock across readback and an atomic metadata registration."""
        _validate_store_input(scope, artifact_id, plaintext, media_type, key)
        if len(plaintext) > self._max_plaintext_bytes:
            raise ValueError("Artifact plaintext exceeds the configured limit.")
        observed_at = _utc(created_at or datetime.now(UTC), "artifact creation time")
        digest = hashlib.sha256(plaintext).hexdigest()
        with self._artifact_lock(scope, artifact_id):
            artifact = self._put_locked(
                scope,
                artifact_id,
                plaintext,
                digest=digest,
                media_type=media_type,
                key=key,
                created_at=observed_at,
            )
            self._read_locked(artifact, key=key, expected_plaintext=plaintext)
            yield artifact

    @contextmanager
    def _artifact_lock(self, scope: Scope, artifact_id: UUID) -> Iterator[None]:
        directory = self._artifact_directory(scope, artifact_id, create=True)
        lock_path = directory / ".lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError:
            raise ArtifactUnavailable("Artifact storage lock is unavailable.") from None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise ArtifactUnavailable("Artifact storage lock is invalid.")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _put_locked(
        self,
        scope: Scope,
        artifact_id: UUID,
        plaintext: bytes,
        *,
        digest: str,
        media_type: str,
        key: ArtifactEncryptionKey,
        created_at: datetime,
    ) -> ArtifactObject:
        object_key = _object_key(scope, artifact_id, digest)
        target = self._path_for_key(object_key)
        existing = self._objects_for_artifact(scope, artifact_id)
        if existing:
            if existing != [target]:
                raise ArtifactConflict("Artifact identity already has different stored evidence.")
            candidate = self._read_header(target, expected_key=object_key)
            _match_object(candidate, digest, len(plaintext), media_type, key.reference)
            self._read_locked(candidate, key=key, expected_plaintext=plaintext)
            return replace(candidate, duplicate=True)

        nonce = os.urandom(12)
        header = {
            "artifact_id": str(artifact_id),
            "byte_length": len(plaintext),
            "created_at": _time_text(created_at),
            "encryption_key_ref": key.reference,
            "media_type": media_type,
            "nonce": _base64url(nonce),
            "schema_version": 1,
            "sha256": digest,
            "site_id": str(scope.site_id),
            "tenant_id": str(scope.tenant_id),
        }
        header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii")
        prefix = _MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
        ciphertext = AESGCM(key.material).encrypt(nonce, plaintext, prefix)
        temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                _write_all(stream, prefix)
                _write_all(stream, ciphertext)
                stream.flush()
                os.fsync(descriptor)
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                candidate = self._read_header(target, expected_key=object_key)
                _match_object(candidate, digest, len(plaintext), media_type, key.reference)
                self._read_locked(candidate, key=key, expected_plaintext=plaintext)
                return replace(candidate, duplicate=True)
            _fsync_directory(target.parent)
        finally:
            os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return ArtifactObject(
            tenant_id=scope.tenant_id,
            site_id=scope.site_id,
            artifact_id=artifact_id,
            object_key=object_key,
            object_version=digest,
            sha256=digest,
            byte_length=len(plaintext),
            media_type=media_type,
            encryption_key_ref=key.reference,
            created_at=created_at,
            duplicate=False,
        )

    def _read_locked(
        self,
        artifact: ArtifactObject | ArtifactRecord,
        *,
        key: ArtifactEncryptionKey,
        expected_plaintext: bytes | None = None,
    ) -> bytes:
        path = self._path_for_key(artifact.object_key)
        raw = self._read_raw(path)
        header_length = struct.unpack(">I", raw[len(_MAGIC) : len(_MAGIC) + 4])[0]
        prefix_length = len(_MAGIC) + 4 + header_length
        header_bytes = raw[len(_MAGIC) + 4 : prefix_length]
        try:
            header = json.loads(header_bytes)
            nonce = _decode_base64url(header["nonce"])
            plaintext = AESGCM(key.material).decrypt(
                nonce, raw[prefix_length:], raw[:prefix_length]
            )
        except (InvalidTag, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ArtifactIntegrityError(
                "Artifact ciphertext failed authenticated verification."
            ) from None
        observed = self._object_from_header(header, artifact.object_key, duplicate=False)
        if _artifact_identity(observed) != _artifact_identity(artifact):
            raise ArtifactIntegrityError("Artifact envelope does not match its immutable identity.")
        if hashlib.sha256(plaintext).hexdigest() != artifact.sha256:
            raise ArtifactIntegrityError("Artifact plaintext hash does not match its identity.")
        if len(plaintext) != artifact.byte_length:
            raise ArtifactIntegrityError("Artifact plaintext length does not match its identity.")
        if expected_plaintext is not None and plaintext != expected_plaintext:
            raise ArtifactConflict("Artifact identity already has different plaintext.")
        return plaintext

    def _read_header(self, path: Path, *, expected_key: str) -> ArtifactObject:
        raw = self._read_raw(path)
        header_length = struct.unpack(">I", raw[len(_MAGIC) : len(_MAGIC) + 4])[0]
        end = len(_MAGIC) + 4 + header_length
        try:
            header = json.loads(raw[len(_MAGIC) + 4 : end])
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ArtifactIntegrityError("Artifact envelope header is invalid.") from None
        return self._object_from_header(header, expected_key, duplicate=False)

    def _object_from_header(
        self, header: object, object_key: str, *, duplicate: bool
    ) -> ArtifactObject:
        try:
            if not isinstance(header, dict) or set(header) != _HEADER_FIELDS:
                raise ValueError
            if header["schema_version"] != 1:
                raise ValueError
            tenant_id = UUID(header["tenant_id"])
            site_id = UUID(header["site_id"])
            artifact_id = UUID(header["artifact_id"])
            created_at = _parse_time(header["created_at"])
            candidate = ArtifactObject(
                tenant_id=tenant_id,
                site_id=site_id,
                artifact_id=artifact_id,
                object_key=object_key,
                object_version=header["sha256"],
                sha256=header["sha256"],
                byte_length=header["byte_length"],
                media_type=header["media_type"],
                encryption_key_ref=header["encryption_key_ref"],
                created_at=created_at,
                duplicate=duplicate,
            )
            _validate_artifact_handle(candidate)
            if object_key != _object_key(Scope(tenant_id, site_id), artifact_id, candidate.sha256):
                raise ValueError
            nonce = _decode_base64url(header["nonce"])
            if len(nonce) != 12:
                raise ValueError
            return candidate
        except (KeyError, TypeError, ValueError):
            raise ArtifactIntegrityError("Artifact envelope metadata is invalid.") from None

    def _read_raw(self, path: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise ArtifactMissing("Artifact object is missing.") from None
        except OSError:
            raise ArtifactUnavailable("Artifact object is unreadable.") from None
        try:
            metadata = os.fstat(descriptor)
            maximum = self._max_plaintext_bytes + _MAX_ENVELOPE_OVERHEAD
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077
                or metadata.st_size < len(_MAGIC) + 4 + 16
                or metadata.st_size > maximum
            ):
                raise ArtifactIntegrityError("Artifact object shape is invalid.")
            raw = bytearray()
            while len(raw) <= maximum:
                chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) != metadata.st_size or not raw.startswith(_MAGIC):
                raise ArtifactIntegrityError("Artifact envelope is invalid.")
            header_length = struct.unpack(">I", raw[len(_MAGIC) : len(_MAGIC) + 4])[0]
            if not 2 <= header_length <= _MAX_HEADER_BYTES:
                raise ArtifactIntegrityError("Artifact envelope header length is invalid.")
            return bytes(raw)
        finally:
            os.close(descriptor)

    def _path_for_key(self, object_key: str) -> Path:
        if not isinstance(object_key, str) or _OBJECT_KEY.fullmatch(object_key) is None:
            raise ArtifactIntegrityError("Artifact object key is invalid.")
        path = self._root.joinpath(*object_key.split("/"))
        try:
            path.relative_to(self._root)
        except ValueError:
            raise ArtifactIntegrityError("Artifact object key escapes storage.") from None
        return path

    def _artifact_directory(self, scope: Scope, artifact_id: UUID, *, create: bool) -> Path:
        if not isinstance(scope, Scope) or not isinstance(artifact_id, UUID):
            raise ValueError("Artifact storage requires typed scope and identity.")
        directory = self._root / "artifacts" / "v1" / str(scope.tenant_id) / str(scope.site_id)
        directory = directory / str(artifact_id)
        if create:
            current = self._root
            for component in directory.relative_to(self._root).parts:
                current = current / component
                current.mkdir(mode=0o700, exist_ok=True)
                self._validate_directory(current)
        return directory

    def _objects_for_artifact(self, scope: Scope, artifact_id: UUID) -> list[Path]:
        directory = self._artifact_directory(scope, artifact_id, create=True)
        objects = []
        for path in directory.iterdir():
            if path.name == ".lock" or path.name.startswith("."):
                continue
            if path.suffix == ".sig":
                objects.append(path)
        return sorted(objects)

    def _scan(self, *, limit: int) -> tuple[list[ArtifactObject], int, int]:
        if not _integer_between(limit, 1, 1000):
            raise ValueError("Artifact cleanup batch size is invalid.")
        base = self._root / "artifacts" / "v1"
        if not base.exists():
            return [], 0, 0
        candidates: list[ArtifactObject] = []
        unreadable = 0
        scanned = 0
        for directory, names, filenames in os.walk(base, followlinks=False):
            names[:] = [name for name in names if not (Path(directory) / name).is_symlink()]
            for filename in sorted(filenames):
                if not filename.endswith(".sig"):
                    continue
                scanned += 1
                path = Path(directory) / filename
                try:
                    object_key = str(path.relative_to(self._root))
                    candidates.append(self._read_header(path, expected_key=object_key))
                except (ArtifactIntegrityError, ArtifactUnavailable, ValueError):
                    unreadable += 1
                if scanned >= limit:
                    return candidates, unreadable, scanned
        return candidates, unreadable, scanned

    def _delete_locked(self, artifact: ArtifactObject) -> None:
        path = self._path_for_key(artifact.object_key)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise ArtifactUnavailable("Orphan artifact object is not a private regular file.")
        path.unlink()
        _fsync_directory(path.parent)

    @staticmethod
    def _validate_directory(path: Path) -> None:
        try:
            metadata = path.lstat()
        except OSError:
            raise ArtifactUnavailable("Artifact storage directory is unavailable.") from None
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
        ):
            raise ArtifactUnavailable("Artifact storage directory must be private and owned.")


def persist_fetch_observation(
    connection: Connection,
    store: EncryptedLocalArtifactStore,
    lease: CrawlFrontierLease,
    result: CrawlFetchResult,
    *,
    started_at: datetime,
    finished_at: datetime,
    network_profile_sha256: str,
    artifact_key: ArtifactEncryptionKey | None,
    retain_until: datetime | None,
    recorded_at: datetime | None = None,
) -> FetchObservationRecorded:
    """Durably register an admitted fetch result and its encrypted body, if any."""
    _validate_fetch_inputs(store, lease, result)
    _require_clean_connection(connection)
    started = _utc(started_at, "fetch start time")
    finished = _utc(finished_at, "fetch finish time")
    recorded = _utc(recorded_at or datetime.now(UTC), "observation recording time")
    current = datetime.now(UTC)
    if (
        finished < started
        or recorded < finished
        or finished > current + timedelta(minutes=5)
        or recorded > current + timedelta(minutes=5)
    ):
        raise ValueError("Fetch observation times are invalid.")
    if (
        not isinstance(network_profile_sha256, str)
        or _SHA256.fullmatch(network_profile_sha256) is None
    ):
        raise ValueError("A canonical network profile SHA-256 is required.")
    _validate_fetch_result(lease, result)
    wall_elapsed_ms = (finished - started).total_seconds() * 1000
    if abs(wall_elapsed_ms - result.elapsed_ms) > 1000:
        raise ValueError("Fetch elapsed time does not match its timestamps.")
    headers = _headers(result.response_headers)
    redirects = _redirects(lease, result)
    tenant_id, site_id = _lease_scope(lease)
    observation_id = _stable_uuid("fetch-observation", str(tenant_id), str(lease.lease_id))
    artifact: ArtifactObject | None = None
    artifact_id: UUID | None = None
    attestation_id: UUID | None = None

    if result.outcome == "fetched":
        if not isinstance(artifact_key, ArtifactEncryptionKey) or retain_until is None:
            raise ValueError("Fetched bodies require an encryption key and retention deadline.")
        retention = _utc(retain_until, "artifact retention deadline")
        artifact_id = _stable_uuid(
            "raw-artifact", str(tenant_id), str(site_id), str(lease.lease_id), result.body_sha256
        )
        attestation_id = _stable_uuid("artifact-attestation", str(artifact_id), "upload-readback")
        typed_scope = Scope(tenant_id, site_id)
        with store.stage_verified(
            typed_scope,
            artifact_id,
            result.body,
            media_type=result.media_type,
            key=artifact_key,
            created_at=recorded,
        ) as artifact:
            if retention <= artifact.created_at:
                raise ValueError("Artifact retention must extend beyond creation.")
            row = _commit_observation(
                connection,
                lease,
                result,
                observation_id=observation_id,
                started_at=started,
                finished_at=finished,
                headers=headers,
                redirects=redirects,
                network_profile_sha256=network_profile_sha256,
                artifact=artifact,
                retain_until=retention,
                attestation_id=attestation_id,
            )
    else:
        if artifact_key is not None or retain_until is not None:
            raise ValueError("Body-free fetch outcomes cannot register artifact material.")
        row = _commit_observation(
            connection,
            lease,
            result,
            observation_id=observation_id,
            started_at=started,
            finished_at=finished,
            headers=headers,
            redirects=redirects,
            network_profile_sha256=network_profile_sha256,
            artifact=None,
            retain_until=None,
            attestation_id=None,
        )
    return _observation_from_row(
        row,
        lease=lease,
        result=result,
        observation_id=observation_id,
        tenant_id=tenant_id,
        site_id=site_id,
        network_profile_sha256=network_profile_sha256,
    )


def load_fetch_observation(
    connection: Connection,
    lease: CrawlFrontierLease,
) -> FetchObservationRecorded | None:
    """Load immutable evidence for one exact historical frontier lease."""
    if not isinstance(lease, CrawlFrontierLease):
        raise ValueError("A validated crawl frontier lease is required.")
    tenant_id, site_id = _lease_scope(lease)
    _require_clean_connection(connection)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT observation_id, crawl_run_id, frontier_id, url_id, fetch_attempt_id, "
            "started_at, finished_at, observation_outcome, http_status, final_url, "
            "response_headers, redirect_chain, resolved_address, media_type, decoded_bytes, "
            "elapsed_ms, network_profile_hash, raw_artifact_id, object_key, object_version, "
            "artifact_hash, artifact_byte_length, artifact_media_type, encryption_key_ref, "
            "artifact_created_at, retain_until, legal_hold, durability_state, lookup_outcome "
            "FROM control.get_fetch_observation_for_lease(%s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                site_id,
                lease.run_id,
                lease.frontier_id,
                lease.lease_id,
                lease.lease_owner,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Fetch observation lookup returned no outcome.")
    if row[28] == "missing":
        return None
    if row[28] != "found":
        raise RuntimeError("Fetch observation lookup returned an invalid outcome.")
    return _observation_from_lookup(row, lease=lease, tenant_id=tenant_id, site_id=site_id)


def load_artifact(
    connection: Connection,
    *,
    tenant_id: UUID,
    site_id: UUID,
    artifact_id: UUID,
) -> ArtifactRecord | None:
    if not all(isinstance(value, UUID) for value in (tenant_id, site_id, artifact_id)):
        raise ValueError("Typed artifact scope and identity are required.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT artifact_id, object_key, object_version, artifact_hash, byte_length, "
            "media_type, encryption_key_ref, created_at, retain_until, legal_hold, "
            "durability_state, outcome FROM control.get_artifact(%s, %s, %s)",
            (tenant_id, site_id, artifact_id),
        ).fetchone()
    if row is None:
        raise RuntimeError("Artifact lookup returned no outcome.")
    if row[11] == "missing":
        return None
    if row[11] != "found":
        raise RuntimeError("Artifact lookup returned an invalid outcome.")
    return _artifact_record_from_values(tenant_id, site_id, row[:11])


def attest_artifact(
    connection: Connection,
    store: EncryptedLocalArtifactStore,
    artifact: ArtifactRecord,
    *,
    key: ArtifactEncryptionKey,
    attestation_id: UUID,
    check_type: str,
    verified_at: datetime | None = None,
) -> ArtifactAttestationRecorded:
    _validate_artifact_handle(artifact)
    _require_clean_connection(connection)
    if not isinstance(store, EncryptedLocalArtifactStore):
        raise ValueError("A validated encrypted artifact store is required.")
    if not isinstance(key, ArtifactEncryptionKey) or key.reference != artifact.encryption_key_ref:
        raise ValueError("The artifact encryption key does not match its reference.")
    if not isinstance(attestation_id, UUID):
        raise ValueError("A typed artifact attestation identity is required.")
    if check_type not in {"scheduled_integrity", "restore_verification"}:
        raise ValueError("Invalid artifact attestation check type.")
    observed_at = _utc(verified_at or datetime.now(UTC), "artifact verification time")
    if observed_at < artifact.created_at or observed_at > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("Artifact verification time is invalid.")
    verified_hash: str | None = None
    try:
        scope = Scope(artifact.tenant_id, artifact.site_id)
        with store._artifact_lock(scope, artifact.artifact_id):
            plaintext = store._read_locked(artifact, key=key)
        verified_hash = hashlib.sha256(plaintext).hexdigest()
        result = "verified"
    except ArtifactIntegrityError:
        result = "corrupt"
    except ArtifactMissing:
        result = "missing"
    except ArtifactUnavailable:
        result = "unreadable"

    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT attestation_id, artifact_id, check_type, result, verified_hash, "
            "verified_at, durability_state, duplicate, outcome "
            "FROM control.record_artifact_attestation(%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                artifact.tenant_id,
                artifact.site_id,
                artifact.artifact_id,
                attestation_id,
                check_type,
                result,
                bytes.fromhex(verified_hash) if verified_hash else None,
                observed_at,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Artifact attestation returned no outcome.")
    if row[8] == "artifact_unavailable":
        raise ArtifactUnavailable("Registered artifact is unavailable.")
    if row[8] == "attestation_conflict":
        raise ArtifactConflict("Artifact attestation identity conflicts.")
    if row[8] != "recorded":
        raise RuntimeError("Artifact attestation returned an invalid outcome.")
    receipt = ArtifactAttestationRecorded(
        attestation_id=row[0],
        artifact_id=row[1],
        check_type=row[2],
        result=row[3],
        verified_sha256=row[4].hex() if row[4] is not None else None,
        verified_at=row[5],
        durability_state=row[6],
        duplicate=row[7],
    )
    if (
        receipt.attestation_id != attestation_id
        or receipt.artifact_id != artifact.artifact_id
        or receipt.check_type != check_type
        or receipt.result != result
        or receipt.verified_sha256 != verified_hash
        or receipt.verified_at != observed_at
        or receipt.durability_state != result
        or not isinstance(receipt.duplicate, bool)
    ):
        raise RuntimeError("Artifact attestation returned mismatched evidence.")
    return receipt


def cleanup_orphan_artifacts(
    connection: Connection,
    store: EncryptedLocalArtifactStore,
    *,
    grace_seconds: int = 3600,
    batch_size: int = 100,
    now: datetime | None = None,
) -> OrphanCleanupResult:
    """Delete only parseable, old objects with no exact SQL registration."""
    _require_clean_connection(connection)
    if not isinstance(store, EncryptedLocalArtifactStore):
        raise ValueError("A validated encrypted artifact store is required.")
    if not _integer_between(grace_seconds, 300, 86_400):
        raise ValueError("Artifact orphan grace period is invalid.")
    observed_at = _utc(now or datetime.now(UTC), "artifact cleanup time")
    candidates, unreadable, scanned = store._scan(limit=batch_size)
    deleted = 0
    registered = 0
    recent = 0
    for candidate in candidates:
        age = (observed_at - candidate.created_at).total_seconds()
        if age < grace_seconds:
            recent += 1
            continue
        scope = Scope(candidate.tenant_id, candidate.site_id)
        with store._artifact_lock(scope, candidate.artifact_id):
            current = load_artifact(
                connection,
                tenant_id=candidate.tenant_id,
                site_id=candidate.site_id,
                artifact_id=candidate.artifact_id,
            )
            if current is not None and _artifact_identity(current) == _artifact_identity(candidate):
                registered += 1
                continue
            store._delete_locked(candidate)
            deleted += 1
    return OrphanCleanupResult(scanned, deleted, registered, recent, unreadable)


def _commit_observation(
    connection: Connection,
    lease: CrawlFrontierLease,
    result: CrawlFetchResult,
    *,
    observation_id: UUID,
    started_at: datetime,
    finished_at: datetime,
    headers: dict[str, str],
    redirects: tuple[str, ...],
    network_profile_sha256: str,
    artifact: ArtifactObject | None,
    retain_until: datetime | None,
    attestation_id: UUID | None,
) -> tuple:
    tenant_id, site_id = _lease_scope(lease)
    artifact_values = (
        (
            artifact.artifact_id,
            artifact.object_key,
            artifact.object_version,
            bytes.fromhex(artifact.sha256),
            artifact.byte_length,
            artifact.media_type,
            artifact.encryption_key_ref,
            artifact.created_at,
            retain_until,
            attestation_id,
        )
        if artifact is not None
        else (None,) * 10
    )
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT observation_id, crawl_run_id, frontier_id, url_id, fetch_attempt_id, "
            "started_at, finished_at, observation_outcome, http_status, final_url, "
            "response_headers, redirect_chain, resolved_address, media_type, decoded_bytes, "
            "elapsed_ms, network_profile_hash, raw_artifact_id, object_key, object_version, "
            "artifact_hash, artifact_byte_length, artifact_media_type, encryption_key_ref, "
            "artifact_created_at, retain_until, legal_hold, durability_state, duplicate, outcome "
            "FROM control.commit_fetch_observation(" + ", ".join(["%s"] * 30) + ")",
            (
                tenant_id,
                site_id,
                lease.run_id,
                lease.frontier_id,
                lease.url_id,
                lease.lease_id,
                lease.lease_owner,
                observation_id,
                started_at,
                finished_at,
                result.outcome,
                result.http_status,
                result.final_url,
                Jsonb(headers),
                Jsonb(list(redirects)),
                result.resolved_address,
                result.media_type,
                result.decoded_bytes,
                result.elapsed_ms,
                bytes.fromhex(network_profile_sha256),
                *artifact_values,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Fetch observation commit returned no outcome.")
    if row[29] == "fetch_unavailable":
        raise FetchObservationUnavailable()
    if row[29] == "observation_conflict":
        raise FetchObservationConflict()
    if row[29] != "recorded":
        raise RuntimeError("Fetch observation commit returned an invalid outcome.")
    return row


def _observation_from_row(
    row: tuple,
    *,
    lease: CrawlFrontierLease,
    result: CrawlFetchResult,
    observation_id: UUID,
    tenant_id: UUID,
    site_id: UUID,
    network_profile_sha256: str,
) -> FetchObservationRecorded:
    artifact = None
    if row[17] is not None:
        artifact = _artifact_record_from_values(tenant_id, site_id, row[17:28])
    receipt = FetchObservationRecorded(
        observation_id=row[0],
        tenant_id=tenant_id,
        site_id=site_id,
        crawl_run_id=row[1],
        frontier_id=row[2],
        url_id=row[3],
        fetch_attempt_id=row[4],
        started_at=row[5],
        finished_at=row[6],
        outcome=row[7],
        http_status=row[8],
        final_url=row[9],
        response_headers=tuple(sorted(row[10].items())),
        redirect_chain=tuple(row[11]),
        resolved_address=row[12],
        media_type=row[13],
        decoded_bytes=row[14],
        elapsed_ms=row[15],
        network_profile_sha256=row[16].hex(),
        raw_artifact=artifact,
        duplicate=row[28],
    )
    if (
        receipt.observation_id != observation_id
        or receipt.crawl_run_id != lease.run_id
        or receipt.frontier_id != lease.frontier_id
        or receipt.url_id != lease.url_id
        or receipt.fetch_attempt_id != lease.lease_id
        or receipt.outcome != result.outcome
        or receipt.http_status != result.http_status
        or receipt.final_url != result.final_url
        or receipt.response_headers != result.response_headers
        or receipt.redirect_chain != result.redirect_chain
        or receipt.resolved_address != result.resolved_address
        or receipt.media_type != result.media_type
        or receipt.decoded_bytes != result.decoded_bytes
        or receipt.elapsed_ms != result.elapsed_ms
        or receipt.network_profile_sha256 != network_profile_sha256
        or (result.outcome == "fetched") != (artifact is not None)
        or not isinstance(receipt.duplicate, bool)
    ):
        raise RuntimeError("Fetch observation commit returned mismatched evidence.")
    return receipt


def _observation_from_lookup(
    row: tuple,
    *,
    lease: CrawlFrontierLease,
    tenant_id: UUID,
    site_id: UUID,
) -> FetchObservationRecorded:
    artifact = None
    if row[17] is not None:
        artifact = _artifact_record_from_values(tenant_id, site_id, row[17:28])
    try:
        headers = tuple(sorted(_headers(tuple(sorted(row[10].items()))).items()))
        redirects = _lookup_redirects(lease, row[11], row[9])
        validate_public_addresses((row[12],))
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError("Fetch observation lookup returned invalid evidence.") from None
    receipt = FetchObservationRecorded(
        observation_id=row[0],
        tenant_id=tenant_id,
        site_id=site_id,
        crawl_run_id=row[1],
        frontier_id=row[2],
        url_id=row[3],
        fetch_attempt_id=row[4],
        started_at=row[5],
        finished_at=row[6],
        outcome=row[7],
        http_status=row[8],
        final_url=row[9],
        response_headers=headers,
        redirect_chain=redirects,
        resolved_address=row[12],
        media_type=row[13],
        decoded_bytes=row[14],
        elapsed_ms=row[15],
        network_profile_sha256=row[16].hex() if isinstance(row[16], bytes) else "",
        raw_artifact=artifact,
        duplicate=False,
    )
    if (
        not isinstance(receipt.observation_id, UUID)
        or receipt.crawl_run_id != lease.run_id
        or receipt.frontier_id != lease.frontier_id
        or receipt.url_id != lease.url_id
        or receipt.fetch_attempt_id != lease.lease_id
        or not _aware(receipt.started_at)
        or not _aware(receipt.finished_at)
        or receipt.finished_at < receipt.started_at
        or receipt.outcome not in _FETCH_OUTCOMES
        or not _integer_between(receipt.http_status, 100, 599)
        or not _integer_between(receipt.decoded_bytes, 0, lease.policy.max_body_bytes)
        or not _integer_between(
            receipt.elapsed_ms, 0, int(lease.policy.total_timeout_seconds * 1000)
        )
        or abs(
            (receipt.finished_at - receipt.started_at).total_seconds() * 1000 - receipt.elapsed_ms
        )
        > 1000
        or _SHA256.fullmatch(receipt.network_profile_sha256) is None
        or (receipt.outcome == "fetched") != (artifact is not None)
        or (
            receipt.outcome == "fetched"
            and (
                receipt.media_type not in {"text/html", "application/xhtml+xml"}
                or receipt.decoded_bytes != artifact.byte_length
            )
        )
        or (receipt.outcome != "fetched" and (receipt.decoded_bytes != 0 or artifact is not None))
    ):
        raise RuntimeError("Fetch observation lookup returned invalid evidence.")
    return receipt


def _artifact_record_from_values(tenant_id: UUID, site_id: UUID, values: tuple) -> ArtifactRecord:
    record = ArtifactRecord(
        tenant_id=tenant_id,
        site_id=site_id,
        artifact_id=values[0],
        object_key=values[1],
        object_version=values[2],
        sha256=values[3].hex(),
        byte_length=values[4],
        media_type=values[5],
        encryption_key_ref=values[6],
        created_at=values[7],
        retain_until=values[8],
        legal_hold=values[9],
        durability_state=values[10],
    )
    _validate_artifact_handle(record)
    if (
        not _aware(record.retain_until)
        or record.retain_until <= record.created_at
        or not isinstance(record.legal_hold, bool)
        or record.durability_state not in {"verified", "missing", "corrupt", "unreadable"}
    ):
        raise RuntimeError("Artifact lookup returned invalid evidence.")
    return record


def _validate_fetch_inputs(store: object, lease: object, result: object) -> None:
    if not isinstance(store, EncryptedLocalArtifactStore):
        raise ValueError("A validated encrypted artifact store is required.")
    if not isinstance(lease, CrawlFrontierLease):
        raise ValueError("A validated crawl frontier lease is required.")
    if not all(
        isinstance(identifier, UUID)
        for identifier in (
            lease.tenant_id,
            lease.site_id,
            lease.run_id,
            lease.frontier_id,
            lease.url_id,
            lease.lease_id,
        )
    ):
        raise ValueError("Crawl frontier lease identity is invalid.")
    if not isinstance(result, CrawlFetchResult):
        raise ValueError("A validated crawl fetch result is required.")


def _validate_fetch_result(lease: CrawlFrontierLease, result: CrawlFetchResult) -> None:
    if result.schema_version != 1 or result.outcome not in _FETCH_OUTCOMES:
        raise ValueError("Unsupported crawl fetch result.")
    if result.original_url not in {lease.url.original_url, lease.url.fetch_url}:
        raise ValueError("Fetch result does not match the leased URL identity.")
    final = lease.policy.admit(result.final_url)
    if result.normalized_key != final.normalized_key or result.final_url != final.fetch_url:
        raise ValueError("Fetch result final URL is not canonical.")
    if not _integer_between(result.http_status, 100, 599):
        raise ValueError("Fetch result HTTP status is invalid.")
    if not _integer_between(result.decoded_bytes, 0, lease.policy.max_body_bytes):
        raise ValueError("Fetch result decoded length is invalid.")
    if not _integer_between(result.elapsed_ms, 0, int(lease.policy.total_timeout_seconds * 1000)):
        raise ValueError("Fetch result elapsed time is invalid.")
    resolved_address = ipaddress.ip_address(result.resolved_address)
    if isinstance(resolved_address, ipaddress.IPv6Address) and resolved_address.ipv4_mapped:
        raise ValueError("IPv4-mapped IPv6 fetch addresses are not supported.")
    validate_public_addresses((result.resolved_address,))
    if result.outcome == "fetched":
        if (
            result.media_type not in {"text/html", "application/xhtml+xml"}
            or not isinstance(result.body, bytes)
            or result.decoded_bytes != len(result.body)
            or not isinstance(result.body_sha256, str)
            or _SHA256.fullmatch(result.body_sha256) is None
            or hashlib.sha256(result.body).hexdigest() != result.body_sha256
        ):
            raise ValueError("Fetched body evidence is invalid.")
    elif result.body != b"" or result.body_sha256 is not None or result.decoded_bytes != 0:
        raise ValueError("Body-free fetch outcome contains unsupported body evidence.")


def _headers(values: object) -> dict[str, str]:
    if not isinstance(values, tuple) or len(values) > len(_RETAINED_HEADERS):
        raise ValueError("Fetch response headers are invalid.")
    result: dict[str, str] = {}
    total = 0
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("Fetch response headers are invalid.")
        name, value = item
        if (
            name not in _RETAINED_HEADERS
            or name in result
            or not isinstance(value, str)
            or not 1 <= len(value) <= 2048
            or _VISIBLE_ASCII.fullmatch(value) is None
        ):
            raise ValueError("Fetch response headers are invalid.")
        total += len(name) + len(value)
        if total > 16384:
            raise ValueError("Fetch response headers exceed their storage limit.")
        result[name] = value
    if tuple(result) != tuple(sorted(result)):
        raise ValueError("Fetch response headers must use canonical ordering.")
    return result


def _redirects(lease: CrawlFrontierLease, result: CrawlFetchResult) -> tuple[str, ...]:
    redirects = result.redirect_chain
    if not isinstance(redirects, tuple) or len(redirects) > lease.policy.max_redirects:
        raise ValueError("Fetch redirect evidence is invalid.")
    seen = {lease.url.normalized_key}
    canonical = []
    for value in redirects:
        url = lease.policy.admit(value)
        if value != url.fetch_url or url.normalized_key in seen:
            raise ValueError("Fetch redirect evidence is invalid.")
        seen.add(url.normalized_key)
        canonical.append(url.fetch_url)
    if canonical:
        if canonical[-1] != result.final_url:
            raise ValueError("Fetch final URL does not match its redirect chain.")
    elif result.final_url != lease.url.fetch_url:
        raise ValueError("Fetch final URL changed without redirect evidence.")
    return tuple(canonical)


def _lookup_redirects(
    lease: CrawlFrontierLease, values: object, final_url: object
) -> tuple[str, ...]:
    if not isinstance(values, list) or not isinstance(final_url, str):
        raise ValueError("Fetch redirect evidence is invalid.")
    redirects = tuple(values)
    if len(redirects) > lease.policy.max_redirects:
        raise ValueError("Fetch redirect evidence is invalid.")
    seen = {lease.url.normalized_key}
    canonical = []
    for value in redirects:
        url = lease.policy.admit(value)
        if value != url.fetch_url or url.normalized_key in seen:
            raise ValueError("Fetch redirect evidence is invalid.")
        seen.add(url.normalized_key)
        canonical.append(url.fetch_url)
    if canonical:
        if canonical[-1] != final_url:
            raise ValueError("Fetch final URL does not match its redirect chain.")
    elif final_url != lease.url.fetch_url:
        raise ValueError("Fetch final URL changed without redirect evidence.")
    return tuple(canonical)


def _lease_scope(lease: CrawlFrontierLease) -> tuple[UUID, UUID]:
    tenant_id = lease.tenant_id
    site_id = lease.site_id
    if not isinstance(tenant_id, UUID) or not isinstance(site_id, UUID):
        raise ValueError("Crawl lease is missing its durable tenant/site identity.")
    return tenant_id, site_id


def _validate_store_input(
    scope: object,
    artifact_id: object,
    plaintext: object,
    media_type: object,
    key: object,
) -> None:
    if not isinstance(scope, Scope) or not isinstance(artifact_id, UUID):
        raise ValueError("Artifact storage requires typed scope and identity.")
    if not isinstance(plaintext, bytes):
        raise ValueError("Artifact plaintext must be bounded bytes.")
    if not isinstance(media_type, str) or _MEDIA_TYPE.fullmatch(media_type) is None:
        raise ValueError("Artifact media type is invalid.")
    if not isinstance(key, ArtifactEncryptionKey):
        raise ValueError("A validated artifact encryption key is required.")


def _validate_artifact_handle(value: object) -> None:
    if (
        not isinstance(value, (ArtifactObject, ArtifactRecord))
        or not all(
            isinstance(identifier, UUID)
            for identifier in (value.tenant_id, value.site_id, value.artifact_id)
        )
        or _OBJECT_KEY.fullmatch(value.object_key) is None
        or _SHA256.fullmatch(value.object_version) is None
        or value.object_version != value.sha256
        or _SHA256.fullmatch(value.sha256) is None
        or not _integer_between(value.byte_length, 0, 1024**3)
        or _MEDIA_TYPE.fullmatch(value.media_type) is None
        or _KEY_REFERENCE.fullmatch(value.encryption_key_ref) is None
        or not _aware(value.created_at)
        or value.object_key
        != _object_key(Scope(value.tenant_id, value.site_id), value.artifact_id, value.sha256)
    ):
        raise ValueError("Artifact handle is invalid.")


def _match_object(
    candidate: ArtifactObject,
    digest: str,
    byte_length: int,
    media_type: str,
    key_reference: str,
) -> None:
    if (
        candidate.sha256 != digest
        or candidate.byte_length != byte_length
        or candidate.media_type != media_type
        or candidate.encryption_key_ref != key_reference
    ):
        raise ArtifactConflict("Artifact identity already has different metadata.")


def _artifact_identity(value: ArtifactObject | ArtifactRecord) -> tuple[object, ...]:
    return (
        value.tenant_id,
        value.site_id,
        value.artifact_id,
        value.object_key,
        value.object_version,
        value.sha256,
        value.byte_length,
        value.media_type,
        value.encryption_key_ref,
        value.created_at,
    )


def _object_key(scope: Scope, artifact_id: UUID, digest: str) -> str:
    return f"artifacts/v1/{scope.tenant_id}/{scope.site_id}/{artifact_id}/{digest}.sig"


def _write_all(stream: BinaryIO, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = stream.write(remaining)
        if written is None or written <= 0:
            raise ArtifactUnavailable("Artifact object write was incomplete.")
        remaining = remaining[written:]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: object) -> bytes:
    if not isinstance(value, str) or _BASE64URL.fullmatch(value) is None:
        raise ValueError
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _time_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    return _utc(parsed, "artifact creation time")


def _utc(value: object, name: str) -> datetime:
    if not _aware(value):
        raise ValueError(f"A timezone-aware {name} is required.")
    return value.astimezone(UTC)


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _integer_between(value: object, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _require_clean_connection(connection: object) -> None:
    if (
        not isinstance(connection, Connection)
        or not connection.autocommit
        or connection.info.transaction_status != TransactionStatus.IDLE
    ):
        raise ValueError("Use an idle autocommit connection for artifact persistence.")


def _stable_uuid(kind: str, *parts: str | None) -> UUID:
    if any(part is None for part in parts):
        raise ValueError("Stable artifact identity inputs are incomplete.")
    return uuid5(_ARTIFACT_ID_NAMESPACE, ":".join((kind, *(part for part in parts if part))))


_MAGIC = b"SIGNAL-ARTIFACT\x00\x01"
_MAX_HEADER_BYTES = 4096
_MAX_ENVELOPE_OVERHEAD = _MAX_HEADER_BYTES + len(_MAGIC) + 4 + 16
_HEADER_FIELDS = {
    "artifact_id",
    "byte_length",
    "created_at",
    "encryption_key_ref",
    "media_type",
    "nonce",
    "schema_version",
    "sha256",
    "site_id",
    "tenant_id",
}
_ARTIFACT_ID_NAMESPACE = UUID("7ca799e8-e1fe-4fb7-b584-3cdf0ee4b7d4")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MEDIA_TYPE = re.compile(r"[a-z0-9!#$&^_.+-]{1,64}/[a-z0-9!#$&^_.+-]{1,64}")
_KEY_REFERENCE = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,254}[a-z0-9]")
_OBJECT_KEY = re.compile(
    r"artifacts/v1/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f]{64}\.sig"
)
_BASE64URL = re.compile(r"[A-Za-z0-9_-]{16}")
_VISIBLE_ASCII = re.compile(r"[\x20-\x7e]+")
_FETCH_OUTCOMES = {
    "fetched",
    "unsupported_encoding",
    "body_limit",
    "unsupported_media_type",
}
_RETAINED_HEADERS = {
    "cache-control",
    "content-encoding",
    "content-length",
    "content-type",
    "etag",
    "last-modified",
    "location",
    "retry-after",
    "transfer-encoding",
}
