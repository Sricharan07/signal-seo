import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from signal_core.crawl_artifacts import (
    ArtifactConflict,
    ArtifactEncryptionKey,
    ArtifactIntegrityError,
    ArtifactUnavailable,
    EncryptedLocalArtifactStore,
)
from signal_core.database import Scope


def key(reference="artifact-key:v1:test"):
    return ArtifactEncryptionKey(reference, bytes(range(32)))


def test_encrypted_store_round_trips_without_plaintext_and_converges_exact_retry(tmp_path):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    scope = Scope(uuid4(), uuid4())
    artifact_id = uuid4()
    body = b"<html><title>private evidence</title></html>"
    created_at = datetime(2026, 9, 9, 12, tzinfo=UTC)

    first = store.put(
        scope,
        artifact_id,
        body,
        media_type="text/html",
        key=key(),
        created_at=created_at,
    )
    duplicate = store.put(
        scope,
        artifact_id,
        body,
        media_type="text/html",
        key=key(),
        created_at=created_at + timedelta(minutes=1),
    )

    assert duplicate == replace(first, duplicate=True)
    assert store.read(first, key=key()) == body
    object_path = store.root.joinpath(*first.object_key.split("/"))
    stored_bytes = object_path.read_bytes()
    assert body not in stored_bytes
    assert b"private evidence" not in stored_bytes
    assert object_path.stat().st_mode & 0o777 == 0o600
    assert store.root.stat().st_mode & 0o077 == 0
    assert "private" not in repr(first)
    assert key().reference not in repr(key())
    assert key().material.hex() not in repr(key())


def test_artifact_identity_cannot_be_reused_for_different_content_or_metadata(tmp_path):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    scope = Scope(uuid4(), uuid4())
    artifact_id = uuid4()
    store.put(scope, artifact_id, b"first", media_type="text/html", key=key())

    with pytest.raises(ArtifactConflict):
        store.put(scope, artifact_id, b"second", media_type="text/html", key=key())
    with pytest.raises(ArtifactConflict):
        store.put(scope, artifact_id, b"first", media_type="application/xhtml+xml", key=key())
    with pytest.raises(ArtifactConflict):
        store.put(
            scope,
            artifact_id,
            b"first",
            media_type="text/html",
            key=key("artifact-key:v1:other"),
        )


def test_authenticated_read_rejects_tampering_and_wrong_key_reference(tmp_path):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    scope = Scope(uuid4(), uuid4())
    stored = store.put(scope, uuid4(), b"evidence", media_type="text/html", key=key())
    path = store.root.joinpath(*stored.object_key.split("/"))
    path.chmod(0o644)
    with pytest.raises(ArtifactIntegrityError):
        store.read(stored, key=key())
    path.chmod(0o600)
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)

    with pytest.raises(ArtifactIntegrityError):
        store.read(stored, key=key())
    with pytest.raises(ArtifactUnavailable):
        store.read(stored, key=key("artifact-key:v1:other"))


def test_store_rejects_symlink_roots_and_object_substitution(tmp_path):
    real_root = tmp_path / "real"
    real_root.mkdir(mode=0o700)
    symlink_root = tmp_path / "link"
    symlink_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(ArtifactUnavailable):
        EncryptedLocalArtifactStore(symlink_root)

    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(ArtifactUnavailable, match="symbolic links"):
        EncryptedLocalArtifactStore(parent_link / "nested")

    store = EncryptedLocalArtifactStore(tmp_path / "objects")
    scope = Scope(uuid4(), uuid4())
    stored = store.put(scope, uuid4(), b"evidence", media_type="text/html", key=key())
    object_path = store.root.joinpath(*stored.object_key.split("/"))
    object_path.unlink()
    object_path.symlink_to(Path("/etc/hosts"))
    with pytest.raises(ArtifactUnavailable):
        store.read(stored, key=key())


@pytest.mark.parametrize(
    ("plaintext", "media_type", "key_value"),
    [
        ("not-bytes", "text/html", key()),
        (b"ok", "Text/HTML", key()),
        (b"ok", "text/html; charset=utf-8", key()),
        (b"ok", "text/html", object()),
    ],
)
def test_store_rejects_malformed_inputs(tmp_path, plaintext, media_type, key_value):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        store.put(
            Scope(uuid4(), uuid4()),
            uuid4(),
            plaintext,
            media_type=media_type,
            key=key_value,
        )


def test_store_enforces_plaintext_limit_and_private_owned_directories(tmp_path):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts", max_plaintext_bytes=1024)
    with pytest.raises(ValueError, match="exceeds"):
        store.put(
            Scope(uuid4(), uuid4()),
            uuid4(),
            os.urandom(1025),
            media_type="text/html",
            key=key(),
        )

    open_root = tmp_path / "open"
    open_root.mkdir(mode=0o755)
    open_root.chmod(0o755)
    with pytest.raises(ArtifactUnavailable, match="private"):
        EncryptedLocalArtifactStore(open_root)
