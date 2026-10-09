"""Closed lockfile-bound registry reads and a bounded, integrity-checked local cache."""

import base64
import binascii
import fcntl
import gzip
import hashlib
import hmac
import io
import os
import re
import stat
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from anyio import sleep, to_thread

from signal_core.astro_source import AstroSourceUnavailable, strict_json
from signal_core.egress_profiles import EgressProfile, npm_tarball_identity
from signal_core.owner_connector_egress import OwnerConnectorContext
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider

MAX_TARBALL_BYTES = 5 * 1024 * 1024
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024


class NpmRegistryUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class LockedTarball:
    url: str
    integrity: str
    name: str
    version: str


@dataclass(frozen=True)
class LockedDependencies:
    lockfile_sha256: str
    tarballs: tuple[LockedTarball, ...]


def locked_dependencies(package: bytes, lockfile: bytes) -> LockedDependencies:
    try:
        root, lock = strict_json(package), strict_json(lockfile)
        packages = lock.get("packages")
        if (
            root.get("workspaces") is not None
            or type(lock.get("lockfileVersion")) is not int
            or lock.get("lockfileVersion") not in (2, 3)
            or not isinstance(packages, dict)
            or not isinstance(packages.get(""), dict)
            or not 2 <= len(packages) <= 513
        ):
            raise ValueError()
        for key in ("dependencies", "devDependencies", "optionalDependencies"):
            if root.get(key, {}) != packages[""].get(key, {}):
                raise ValueError()
        tarballs = {}
        for path, entry in packages.items():
            if path == "":
                continue
            if (
                not isinstance(entry, dict)
                or re.fullmatch(
                    r"(?:node_modules/(?:@[a-z0-9._-]+/)?[a-z0-9._-]+/)*node_modules/(?:@[a-z0-9._-]+/)?[a-z0-9._-]+",
                    path,
                )
                is None
                or entry.get("link")
                or not isinstance(entry.get("resolved"), str)
                or not isinstance(entry.get("integrity"), str)
            ):
                raise ValueError()
            name, version = npm_tarball_identity(entry["resolved"])
            if path.split("node_modules/")[-1] != name or entry.get("version") != version:
                raise ValueError()
            _integrity(entry["integrity"])
            item = LockedTarball(entry["resolved"], entry["integrity"], name, version)
            if item.url in tarballs and tarballs[item.url] != item:
                raise ValueError()
            tarballs[item.url] = item
    except (ValueError, TypeError, KeyError, AstroSourceUnavailable):
        raise NpmRegistryUnavailable("NPM_LOCKFILE_REJECTED") from None
    return LockedDependencies(
        hashlib.sha256(lockfile).hexdigest(), tuple(tarballs[url] for url in sorted(tarballs))
    )


def _integrity(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"sha(?:256|512)-[A-Za-z0-9+/]+={0,2}", value) is None
    ):
        raise ValueError()
    algorithm, encoded = value.split("-", 1)
    digest = base64.b64decode(encoded, validate=True)
    if (
        len(digest) != {"sha256": 32, "sha512": 64}[algorithm]
        or base64.b64encode(digest).decode() != encoded
    ):
        raise ValueError()
    return algorithm, digest


class _ExpandedReader:
    def __init__(self, reader):
        self.reader, self.remaining = reader, 64 * 1024 * 1024

    def read(self, size):
        chunk = self.reader.read(min(size, self.remaining + 1))
        self.remaining -= len(chunk)
        if self.remaining < 0:
            raise NpmRegistryUnavailable("NPM_TARBALL_EXPANSION_REJECTED")
        return chunk


def verify_tarball(item: LockedTarball, content: bytes) -> int:
    try:
        algorithm, expected = _integrity(item.integrity)
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= MAX_TARBALL_BYTES
            or not hmac.compare_digest(hashlib.new(algorithm, content).digest(), expected)
        ):
            raise NpmRegistryUnavailable("NPM_INTEGRITY_REJECTED")
        total, names, package = 0, set(), None
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as compressed:
            reader = _ExpandedReader(compressed)
            with tarfile.open(fileobj=reader, mode="r|") as archive:
                for member in archive:
                    path = member.name.removesuffix("/")
                    if (
                        not path.isascii()
                        or len(path) > 1024
                        or not (
                            path.startswith("package/") or (path == "package" and member.isdir())
                        )
                        or any(part in {"", ".", ".."} for part in path.split("/"))
                        or path in names
                        or not (member.isdir() or member.isfile())
                        or member.mode & 0o7000
                        or len(names) >= 4096
                        or not 0 <= member.size <= 16 * 1024 * 1024
                    ):
                        raise NpmRegistryUnavailable("NPM_TARBALL_REJECTED")
                    names.add(path)
                    total += member.size
                    if total > 64 * 1024 * 1024:
                        raise NpmRegistryUnavailable("NPM_TARBALL_EXPANSION_REJECTED")
                    if path == "package/package.json" and member.isfile():
                        if member.size > 128 * 1024:
                            raise ValueError()
                        package = strict_json(archive.extractfile(member).read())
            while reader.read(64 * 1024):
                pass
        if (
            package is None
            or package.get("name") != item.name
            or package.get("version") != item.version
        ):
            raise NpmRegistryUnavailable("NPM_TARBALL_IDENTITY_REJECTED")
        return 64 * 1024 * 1024 - reader.remaining
    except NpmRegistryUnavailable:
        raise
    except (ValueError, OSError, EOFError, binascii.Error, tarfile.TarError):
        raise NpmRegistryUnavailable("NPM_TARBALL_REJECTED") from None


class NpmRegistryCache:
    def __init__(self, *, worktree_root: Path):
        root = worktree_root.resolve()
        self.directory = root / ".runtime" / "npm-registry"
        if self.directory.resolve() != self.directory or self.directory.is_symlink():
            raise NpmRegistryUnavailable("NPM_CACHE_SCOPE_REJECTED")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _read(self, key):
        try:
            fd = os.open(self.directory / key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError:
            raise NpmRegistryUnavailable("NPM_CACHE_REJECTED") from None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TARBALL_BYTES:
                raise NpmRegistryUnavailable("NPM_CACHE_REJECTED")
            return stream.read(MAX_TARBALL_BYTES + 1)

    def _save(self, key, content):
        lock = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(lock).st_mode):
                raise NpmRegistryUnavailable("NPM_CACHE_REJECTED")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            entries = []
            for path in self.directory.iterdir():
                if path.name == ".lock":
                    continue
                info = path.lstat()
                if re.fullmatch("[0-9a-f]{64}[.]tgz", path.name) is None or not stat.S_ISREG(
                    info.st_mode
                ):
                    raise NpmRegistryUnavailable("NPM_CACHE_REJECTED")
                entries.append((info.st_mtime_ns, path, info.st_size))
            total = sum(entry[2] for entry in entries)
            count = len(entries)
            if any(path.name == key for _, path, _ in entries):
                return
            for _, path, size in sorted(entries):
                if total + len(content) <= MAX_CACHE_BYTES and count < 1024:
                    break
                path.unlink()
                total -= size
                count -= 1
            target = self.directory / key
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                return
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(content)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        except OSError:
            raise NpmRegistryUnavailable("NPM_CACHE_REJECTED") from None
        finally:
            os.close(lock)

    async def fetch(self, manifest: LockedDependencies, provider: SharedEgressProvider):
        if (
            not isinstance(provider, SharedEgressProvider)
            or isinstance(provider.run, OwnerConnectorContext)
            or provider.purpose != "connector"
            or provider.policy.allowed_origins != ("https://registry.npmjs.org",)
            or provider.policy.max_redirects != 0
        ):
            raise NpmRegistryUnavailable("NPM_EGRESS_UNAVAILABLE")
        deadline, total, expanded, result = time.monotonic() + 600, 0, 0, []
        for item in manifest.tarballs:
            key = hashlib.sha256(item.integrity.encode("ascii")).hexdigest() + ".tgz"
            content = self._read(key)
            if content is None:
                while True:
                    if time.monotonic() >= deadline:
                        raise NpmRegistryUnavailable("NPM_FETCH_TIMEOUT")

                    def fetch_one(item=item):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise NpmRegistryUnavailable("NPM_FETCH_TIMEOUT")
                        return provider.request_json(
                            method="GET",
                            url=item.url,
                            profile=EgressProfile.NPM_REGISTRY,
                            authorization=None,
                            operation_id=uuid4(),
                            timeout_seconds=min(5, remaining),
                            max_response_bytes=MAX_TARBALL_BYTES,
                        )

                    try:
                        response = await to_thread.run_sync(fetch_one)
                    except ProviderEgressUnavailable as error:
                        if error.code == "EGRESS_DEFERRED":
                            await sleep(1)
                            continue
                        raise NpmRegistryUnavailable("NPM_EGRESS_UNAVAILABLE") from None
                    if response.status_code != 200:
                        raise NpmRegistryUnavailable("NPM_REGISTRY_RESPONSE_REJECTED")
                    content = response.body
                    break
            expanded += verify_tarball(item, content)
            total += len(content)
            if (
                total > MAX_CACHE_BYTES
                or expanded > MAX_EXPANDED_BYTES
                or time.monotonic() >= deadline
            ):
                raise NpmRegistryUnavailable("NPM_DEPENDENCY_BUDGET_EXCEEDED")
            self._save(key, content)
            result.append((key, content))
        return tuple(result)
