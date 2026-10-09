"""Restore an encrypted test snapshot to an already initialized disposable clone."""

import argparse
import sys
import time
from pathlib import Path

import httpx2
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from integration_secrets import (
    OperatorError,
    Store,
    check_audit,
    private_directory,
    read_private,
    recovery,
    unseal,
)


def decrypt_snapshot(directory: Path) -> bytes:
    key = read_private(directory / "snapshot-key.bin")
    content = read_private(directory / "snapshot.enc", maximum=64 * 1024**2 + 28)
    return AESGCM(key).decrypt(content[:12], content[12:], b"signal-test-snapshot-v1")


def distinct_clusters(source: dict, target: dict) -> None:
    source_id = source.get("cluster_id")
    target_id = target.get("cluster_id")
    if (
        not isinstance(source_id, str)
        or not source_id
        or not isinstance(target_id, str)
        or not target_id
        or source_id == target_id
        or target.get("sealed") is not False
    ):
        raise OperatorError(
            "Restore requires a distinct, initialized, unsealed disposable cluster."
        )


def qualify(source: Store, target: Store, source_directory: Path, target_directory: Path) -> None:
    if source_directory == target_directory or source.client.base_url == target.client.base_url:
        raise OperatorError("Source and disposable restore target must be distinct.")
    snapshot = decrypt_snapshot(source_directory)
    source_root = recovery(source_directory)["root_token"]
    target_root = recovery(target_directory)["root_token"]
    distinct_clusters(
        source.request("GET", "/sys/seal-status"),
        target.request("GET", "/sys/seal-status"),
    )
    check_audit(source, source_root)
    check_audit(target, target_root)
    target.request(
        "POST",
        "/sys/mounts/signal-restore-negative",
        token=target_root,
        payload={"type": "kv", "options": {"version": "2"}},
    )
    target.request(
        "POST",
        "/signal-restore-negative/data/canary",
        token=target_root,
        payload={"data": {"value": "synthetic-created-after-backup"}},
    )
    with target.client.stream(
        "POST",
        "/v1/sys/storage/raft/snapshot-force",
        content=snapshot,
        headers={"X-Vault-Token": target_root, "Content-Type": "application/octet-stream"},
        timeout=90,
    ) as reply:
        if reply.status_code not in (200, 204):
            raise OperatorError(
                f"Disposable snapshot restore failed with HTTP {reply.status_code}."
            )
    deadline = time.monotonic() + 90
    while True:
        try:
            state = target.request("GET", "/sys/seal-status")
            if state.get("sealed") is True:
                unseal(target, source_directory)
            restored = target.request("GET", "/sys/mounts", token=source_root)
            break
        except (OperatorError, httpx2.HTTPError):
            if time.monotonic() >= deadline:
                raise OperatorError(
                    "Disposable restored cluster did not accept recovered authority."
                ) from None
            time.sleep(0.5)
    if "signal-restore-negative/" in restored.get("data", restored):
        raise OperatorError("Disposable post-backup state survived the restore.")
    target.request("GET", "/sys/mounts", token=target_root, expected=(403,))
    check_audit(target, source_root)
    # The primary store must still accept its original operator authority.
    check_audit(source, source_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--target-directory", type=Path, required=True)
    parser.add_argument("--source-url", default="https://localhost:18200")
    parser.add_argument("--target-url", default="https://localhost:18201")
    parser.add_argument("--confirm-disposable-target", action="store_true", required=True)
    args = parser.parse_args()
    source = target = None
    try:
        source_directory = private_directory(args.source_directory)
        target_directory = private_directory(args.target_directory)
        source = Store(args.source_url, source_directory / "ca.pem")
        target = Store(args.target_url, target_directory / "ca.pem")
        qualify(source, target, source_directory, target_directory)
        print("Encrypted snapshot restore: PASS on distinct disposable cluster.")
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, OperatorError) else type(error).__name__
        print(
            f"Snapshot restore failed ({reason}); credentials suppressed; keep target isolated.",
            file=sys.stderr,
        )
        return 1
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


if __name__ == "__main__":
    raise SystemExit(main())
