"""Owner-document ingestion and typed untrusted read boundary."""

import hashlib
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from psycopg import Connection

from signal_core.crawl_artifacts import (
    ArtifactEncryptionKey,
    ArtifactIntegrityError,
    ArtifactRecord,
    ArtifactUnavailable,
    EncryptedLocalArtifactStore,
)
from signal_core.database import Scope, _clean_transaction
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import hash_session_token

MAX_FILE_BYTES = 2 * 1024 * 1024
_KINDS = {
    ".pdf": ("pdf", "application/pdf"),
    ".docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".md": ("markdown", "text/markdown"),
    ".txt": ("text", "text/plain"),
}
_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|secret|password|access[_-]?token|client[_-]?secret)\s*[:=]\s*\S{6,})|"
    r"(?i:(?:Bearer|Basic)\s+[A-Za-z0-9+/=_-]{12,})|"
    r"(?i:(?:postgres(?:ql)?|mongodb|redis)://[^\s@]+:[^\s@]+@)|"
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[psu]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
    r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{16,})\b"
)
_INJECTION = re.compile(
    r"(?i)(?:ignore (?:all |any )?(?:previous|prior|system) instructions|"
    r"(?:system|developer|assistant)\s*(?:prompt|message|instructions)\s*[:=]|"
    r"you are (?:now|an? (?:assistant|agent))|"
    r"do not (?:tell|show|reveal) (?:the )?(?:user|owner)|"
    r"<\|(?:im_start|system)\|>|(?:send|exfiltrate) (?:the )?(?:secret|token|credential))"
)


class BrandDocumentRejected(ValueError):
    """The upload is unsafe, invalid, or exceeds a fixed bound."""


class BrandDocumentUnavailable(RuntimeError):
    """Storage, sandbox, or current owner authority is unavailable."""


@dataclass(frozen=True)
class BrandDocument:
    document_id: UUID
    display_name: str
    media_type: str
    created_at: datetime
    supersedes_id: UUID | None
    injection_signal: bool
    secret_signal: bool
    deleted: bool
    retained_for_evidence: bool


@dataclass(frozen=True, repr=False)
class UntrustedDocumentText:
    """Never pass this content as a system/developer instruction or approved fact."""

    document_id: UUID
    tenant_id: UUID
    site_id: UUID
    text: str
    text_sha256: str
    injection_signal: bool
    trust_label: str = "owner_upload_untrusted_data"


def _kind_and_name(filename: str, body: bytes) -> tuple[str, str, str]:
    if not isinstance(filename, str) or not 1 <= len(filename) <= 120:
        raise BrandDocumentRejected("invalid_name")
    if filename != Path(filename).name or any(ord(c) < 32 for c in filename):
        raise BrandDocumentRejected("invalid_name")
    if _SECRET.search(filename):
        raise BrandDocumentRejected("secret_in_filename")
    suffix = Path(filename).suffix.lower()
    if suffix not in _KINDS:
        raise BrandDocumentRejected("unsupported_type")
    if not isinstance(body, bytes) or not 1 <= len(body) <= MAX_FILE_BYTES:
        raise BrandDocumentRejected("file_size_limit")
    if body.startswith((b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"#!")):
        raise BrandDocumentRejected("executable_content")
    kind, media_type = _KINDS[suffix]
    if kind == "pdf" and not body.startswith(b"%PDF-"):
        raise BrandDocumentRejected("spoofed_type")
    if kind == "docx" and not body.startswith(b"PK\x03\x04"):
        raise BrandDocumentRejected("spoofed_type")
    if kind in {"text", "markdown"}:
        if body.startswith((b"PK\x03\x04", b"%PDF-", b"\x1f\x8b", b"BZh")) or b"\x00" in body:
            raise BrandDocumentRejected("spoofed_type")
        try:
            body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise BrandDocumentRejected("invalid_text_encoding") from None
    return kind, media_type, filename


def _limit_process() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))


def extract_document(body: bytes, filename: str, *, timeout_seconds: float = 12) -> tuple[str, str]:
    """Run parser without network, bounded by process limits and a wall timeout."""
    kind, media_type, _ = _kind_and_name(filename, body)
    worker = Path(__file__).with_name("brand_document_worker.py")
    if kind == "pdf" and (not shutil.which("pdftotext") or not shutil.which("pdfinfo")):
        raise BrandDocumentUnavailable("pdf_parser_unavailable")
    with tempfile.TemporaryDirectory(prefix="signal-brand-") as directory:
        path = Path(directory) / "input"
        path.write_bytes(body)
        os.chmod(path, 0o600)
        if sys.platform == "darwin" and shutil.which("sandbox-exec"):
            profile = (
                "(version 1)(allow default)(deny network*)"
                f"(deny file-read* (subpath {json.dumps(str(Path.home()))}))"
                f"(allow file-read* (subpath {json.dumps(str(Path(sys.prefix).resolve()))}))"
                f"(allow file-read* (literal {json.dumps(str(worker.resolve()))}))"
            )
            launcher = ["sandbox-exec", "-p", profile]
            command = [*launcher, str(Path(sys.executable).resolve()), str(worker), str(path), kind]
        elif sys.platform.startswith("linux") and shutil.which("bwrap"):
            launcher = ["bwrap", "--unshare-net", "--die-with-parent", "--tmpfs", "/"]
            for directory_name in ("/usr", "/lib", "/lib64", "/bin"):
                if Path(directory_name).exists():
                    launcher.extend(("--ro-bind", directory_name, directory_name))
            launcher.extend(
                (
                    "--ro-bind",
                    str(worker),
                    "/worker.py",
                    "--ro-bind",
                    str(path),
                    "/input",
                    "--proc",
                    "/proc",
                    "--dev",
                    "/dev",
                    "--tmpfs",
                    "/tmp",
                    "--chdir",
                    "/",
                )
            )
            command = [*launcher, str(Path(sys.executable).resolve()), "/worker.py", "/input", kind]
        else:
            raise BrandDocumentUnavailable("document_sandbox_unavailable")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                cwd="/",
                env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                timeout=timeout_seconds,
                check=False,
                preexec_fn=_limit_process,
            )
        except subprocess.TimeoutExpired:
            raise BrandDocumentRejected("extraction_timeout") from None
        except OSError:
            raise BrandDocumentUnavailable("document_sandbox_unavailable") from None
    if result.returncode != 0:
        reason = result.stderr.decode("ascii", errors="ignore")[:64]
        raise BrandDocumentRejected(
            reason if re.fullmatch(r"[a-z_]{3,64}", reason) else "extraction_failed"
        )
    if not 1 <= len(result.stdout) <= 512 * 1024:
        raise BrandDocumentRejected("extracted_text_limit")
    try:
        text = result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise BrandDocumentRejected("invalid_extracted_text") from None
    return text, media_type


def _session(session_token: str, generation: str) -> tuple[bytes, str]:
    return hash_session_token(session_token), validate_recovery_generation(generation)


def upload_brand_document(
    connection: Connection,
    store: EncryptedLocalArtifactStore,
    key: ArtifactEncryptionKey,
    *,
    session_token: str,
    current_recovery_generation: str,
    site_id: UUID,
    filename: str,
    body: bytes,
    supersedes_id: UUID | None = None,
    on_registered: Callable[[Connection, UUID], None] | None = None,
) -> BrandDocument:
    if not isinstance(site_id, UUID) or (
        supersedes_id is not None and not isinstance(supersedes_id, UUID)
    ):
        raise BrandDocumentRejected("invalid_document_scope")
    if not isinstance(store, EncryptedLocalArtifactStore) or not isinstance(
        key, ArtifactEncryptionKey
    ):
        raise BrandDocumentUnavailable("document_storage_unavailable")
    kind, media_type, name = _kind_and_name(filename, body)
    session_hash, generation = _session(session_token, current_recovery_generation)
    with _clean_transaction(connection):
        preflight = connection.execute(
            "SELECT outcome, tenant_id FROM control.prepare_brand_document_upload(%s, %s, %s, %s)",
            (session_hash, generation, site_id, supersedes_id),
        ).fetchone()
    if preflight is None or preflight[0] == "denied":
        raise BrandDocumentUnavailable("owner_access_denied")
    if preflight[0] != "ready":
        raise BrandDocumentRejected(preflight[0])
    text, extracted_type = extract_document(body, name)
    if extracted_type != media_type:
        raise BrandDocumentRejected("spoofed_type")
    document_id, artifact_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    try:
        with store.stage_verified(
            Scope(preflight[1], site_id),
            artifact_id,
            body,
            media_type=media_type,
            key=key,
            created_at=now,
        ) as artifact:
            with _clean_transaction(connection):
                outcome = connection.execute(
                    "SELECT control.register_brand_document(%s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        session_hash,
                        generation,
                        site_id,
                        document_id,
                        artifact_id,
                        name,
                        media_type,
                        hashlib.sha256(text.encode()).digest(),
                        bool(_INJECTION.search(text)),
                        bool(_SECRET.search(text)),
                        artifact.object_key,
                        artifact.object_version,
                        bytes.fromhex(artifact.sha256),
                        artifact.byte_length,
                        key.reference,
                        now,
                        now + timedelta(days=30),
                        supersedes_id,
                    ),
                ).fetchone()[0]
                if outcome == "registered" and on_registered is not None:
                    on_registered(connection, document_id)
    except (OSError, ArtifactUnavailable, ArtifactIntegrityError):
        raise BrandDocumentUnavailable("document_storage_unavailable") from None
    if outcome != "registered":
        raise BrandDocumentRejected(outcome)
    return BrandDocument(
        document_id,
        name,
        media_type,
        now,
        supersedes_id,
        bool(_INJECTION.search(text)),
        bool(_SECRET.search(text)),
        False,
        True,
    )


def list_brand_documents(
    connection: Connection, *, session_token: str, current_recovery_generation: str, site_id: UUID
) -> tuple[BrandDocument, ...]:
    session_hash, generation = _session(session_token, current_recovery_generation)
    with _clean_transaction(connection):
        owner = connection.execute(
            "SELECT tenant_id FROM control.brand_document_owner(%s, %s, %s)",
            (session_hash, generation, site_id),
        ).fetchone()
        if owner is None:
            raise BrandDocumentUnavailable("owner_access_denied")
        rows = connection.execute(
            "SELECT * FROM control.list_brand_documents(%s, %s, %s)",
            (session_hash, generation, site_id),
        ).fetchall()
    return tuple(BrandDocument(*row) for row in rows)


def delete_brand_document(
    connection: Connection,
    *,
    session_token: str,
    current_recovery_generation: str,
    site_id: UUID,
    document_id: UUID,
) -> str:
    session_hash, generation = _session(session_token, current_recovery_generation)
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.delete_brand_document(%s, %s, %s, %s, %s)",
            (session_hash, generation, site_id, document_id, uuid4()),
        ).fetchone()[0]
    return outcome


def read_brand_document(
    connection: Connection,
    store: EncryptedLocalArtifactStore,
    key: ArtifactEncryptionKey,
    *,
    session_token: str,
    current_recovery_generation: str,
    site_id: UUID,
    document_id: UUID,
) -> UntrustedDocumentText:
    """Owner-only typed read port; secret-bearing documents are never returned."""
    session_hash, generation = _session(session_token, current_recovery_generation)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.read_brand_document_artifact(%s, %s, %s, %s)",
            (session_hash, generation, site_id, document_id),
        ).fetchone()
    if row is None:
        raise BrandDocumentUnavailable("document_unavailable")
    if row[14]:
        raise BrandDocumentUnavailable("document_contains_secret")
    artifact = ArtifactRecord(
        row[0],
        site_id,
        row[1],
        row[2],
        row[3],
        row[4].hex(),
        row[5],
        row[6],
        row[7],
        row[8],
        row[9],
        row[10],
        row[11],
    )
    try:
        body = store.read(artifact, key=key)
    except (OSError, ArtifactUnavailable, ArtifactIntegrityError):
        raise BrandDocumentUnavailable("document_storage_unavailable") from None
    text, _ = extract_document(
        body,
        "document"
        + next(
            extension for extension, (_, media) in _KINDS.items() if media == artifact.media_type
        ),
    )
    digest = hashlib.sha256(text.encode()).digest()
    if digest != row[12]:
        raise BrandDocumentUnavailable("document_integrity_failed")
    return UntrustedDocumentText(document_id, row[0], site_id, text, digest.hex(), row[13])
