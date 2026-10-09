"""Verified platform recipe releases; no tenant mutation or execution authority."""

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from psycopg import Connection, Error

VERIFIED_HOMEPAGE_METADATA_RELEASE_ID = UUID("9f2c4164-64a6-4e19-a2d0-3d9b16c8e701")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_CURRENT_REVIEWED = frozenset({"REVIEWED"})


class RecipeReleaseUnavailable(Exception):
    """A release or range cannot be used as reviewed authority."""


class RecipeReleaseConflict(Exception):
    """A duplicate identity, stale transition, or invalid lifecycle move."""


@dataclass(frozen=True, order=True)
class RecipeVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "RecipeVersion":
        if not isinstance(value, str):
            raise ValueError("Recipe version is invalid.")
        match = _VERSION.fullmatch(value)
        if match is None:
            raise ValueError("Recipe version is invalid.")
        parts = tuple(int(part) for part in match.groups())
        if any(part > 2_147_483_647 for part in parts):
            raise ValueError("Recipe version is invalid.")
        return cls(*parts)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class RecipeRelease:
    id: UUID
    recipe_key: str
    version: RecipeVersion
    status: str
    content_hash: str
    manifest: dict

    @property
    def delivery_mode(self) -> str:
        return self.manifest["delivery_mode"]


def _validate_body(body: bytes, release_id: UUID, recipe_key: str, version: RecipeVersion) -> dict:
    try:
        manifest = json.loads(body)
        if not isinstance(manifest, dict) or rfc8785.dumps(manifest) != body:
            raise ValueError
        if (
            manifest.get("schema_version") != 1
            or manifest.get("kind") != "recipe"
            or manifest.get("release_id") != str(release_id)
            or manifest.get("recipe_key") != recipe_key
            or manifest.get("version") != str(version)
            or type(manifest.get("contract_version")) is not int
            or manifest["contract_version"] < 1
            or manifest.get("delivery_mode") not in {"proposal_only", "pull_request"}
            or type(manifest.get("max_resources_per_revision")) is not int
            or not 1 <= manifest["max_resources_per_revision"] <= 100
        ):
            raise ValueError
        for field in (
            "allowed_fields",
            "allowed_resource_types",
            "required_evidence",
            "steps",
            "verification_assertions",
        ):
            values = manifest.get(field)
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 64
                or any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError
        for field in ("purpose", "approval_class", "recovery_mode"):
            if not isinstance(manifest.get(field), str) or not 1 <= len(manifest[field]) <= 500:
                raise ValueError
        return manifest
    except (ValueError, TypeError, KeyError):
        raise RecipeReleaseUnavailable("Invalid signed recipe release.") from None


def _verified_release(row: tuple) -> RecipeRelease:
    (
        release_id,
        recipe_key,
        major,
        minor,
        patch,
        contract_version,
        body,
        content_hash,
        _key_id,
        signature,
        public_key,
        status,
    ) = row
    try:
        if (
            not isinstance(release_id, UUID)
            or not isinstance(recipe_key, str)
            or _KEY.fullmatch(recipe_key) is None
            or any(type(value) is not int or value < 0 for value in (major, minor, patch))
            or type(contract_version) is not int
            or contract_version < 1
        ):
            raise ValueError
        version = RecipeVersion(major, minor, patch)
        body, content_hash = bytes(body), bytes(content_hash)
        if sha256(body).digest() != content_hash:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(bytes(public_key)).verify(bytes(signature), body)
        manifest = _validate_body(body, release_id, recipe_key, version)
        if manifest["contract_version"] != contract_version:
            raise ValueError
        return RecipeRelease(release_id, recipe_key, version, status, content_hash.hex(), manifest)
    except (InvalidSignature, ValueError, TypeError, KeyError):
        raise RecipeReleaseUnavailable("Recipe release verification failed.") from None


def register_recipe_signing_key(connection: Connection, *, key_id: str, public_key: bytes) -> None:
    if not isinstance(key_id, str) or re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", key_id) is None:
        raise ValueError("Recipe signing key ID is invalid.")
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise ValueError("Recipe signing public key is invalid.")
    with connection.transaction():
        connection.execute(
            "SELECT control.register_recipe_signing_key(%s,%s)", (key_id, public_key)
        )


def register_recipe_release(
    connection: Connection,
    *,
    release_id: UUID,
    recipe_key: str,
    version: str,
    canonical_body: bytes,
    signing_key_id: str,
    signature: bytes,
    actor_user_id: UUID,
    event_id: UUID | None = None,
) -> None:
    if not isinstance(release_id, UUID) or not isinstance(actor_user_id, UUID):
        raise ValueError("Recipe release identity is invalid.")
    if not isinstance(recipe_key, str) or _KEY.fullmatch(recipe_key) is None:
        raise ValueError("Recipe family is invalid.")
    parsed = RecipeVersion.parse(version)
    if not isinstance(canonical_body, bytes) or not 1 <= len(canonical_body) <= 16384:
        raise ValueError("Recipe release body is invalid.")
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("Recipe release signature is invalid.")
    manifest = _validate_body(canonical_body, release_id, recipe_key, parsed)
    with connection.transaction():
        key_row = connection.execute(
            "SELECT control.recipe_signing_key_bytes(%s)", (signing_key_id,)
        ).fetchone()
        if key_row is None or key_row[0] is None:
            raise RecipeReleaseUnavailable("Recipe signing key unavailable.")
        try:
            Ed25519PublicKey.from_public_bytes(bytes(key_row[0])).verify(signature, canonical_body)
        except (InvalidSignature, ValueError):
            raise RecipeReleaseUnavailable("Recipe signature invalid.") from None
        try:
            connection.execute(
                "SELECT control.register_recipe_release(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    release_id,
                    recipe_key,
                    parsed.major,
                    parsed.minor,
                    parsed.patch,
                    manifest["contract_version"],
                    canonical_body,
                    signing_key_id,
                    signature,
                    event_id or uuid4(),
                    actor_user_id,
                ),
            )
        except Error as error:
            if error.sqlstate in {"23505", "23514"}:
                raise RecipeReleaseConflict("Recipe release identity conflict.") from None
            raise


def transition_recipe_release(
    connection: Connection,
    *,
    release_id: UUID,
    expected_status: str,
    new_status: str,
    actor_user_id: UUID,
    reason: str,
    event_id: UUID | None = None,
) -> int:
    if (
        not isinstance(release_id, UUID)
        or not isinstance(actor_user_id, UUID)
        or not isinstance(reason, str)
        or not 1 <= len(reason) <= 500
        or not isinstance(expected_status, str)
        or not isinstance(new_status, str)
    ):
        raise ValueError("Recipe release transition is invalid.")
    try:
        with connection.transaction():
            row = connection.execute(
                "SELECT control.transition_recipe_release(%s,%s,%s,%s,%s,%s)",
                (
                    release_id,
                    expected_status,
                    new_status,
                    event_id or uuid4(),
                    actor_user_id,
                    reason,
                ),
            ).fetchone()
            if row is None or type(row[0]) is not int:
                raise RecipeReleaseUnavailable("Recipe transition unavailable.")
            return row[0]
    except Error as error:
        if error.sqlstate in {"23505", "23514"}:
            raise RecipeReleaseConflict("Recipe release transition conflict.") from None
        raise


def _candidates(connection: Connection, recipe_key: str) -> tuple[RecipeRelease, ...]:
    try:
        rows = connection.execute(
            "SELECT * FROM control.recipe_release_candidates(%s)", (recipe_key,)
        ).fetchall()
    except Error:
        raise RecipeReleaseUnavailable("Recipe family unavailable.") from None
    return tuple(_verified_release(row) for row in rows)


def resolve_reviewed_recipe_range(
    connection: Connection,
    *,
    recipe_key: str,
    minimum_inclusive: str,
    maximum_exclusive: str,
) -> tuple[UUID, ...]:
    """Call inside the future grant transaction and persist this exact ID tuple."""
    if not isinstance(recipe_key, str) or _KEY.fullmatch(recipe_key) is None:
        raise ValueError("Recipe family is invalid.")
    minimum = RecipeVersion.parse(minimum_inclusive)
    maximum = RecipeVersion.parse(maximum_exclusive)
    if (
        minimum >= maximum
        or maximum.major not in {minimum.major, minimum.major + 1}
        or (maximum.major != minimum.major and (maximum.minor, maximum.patch) != (0, 0))
    ):
        raise ValueError("Recipe range must be compatible and bounded.")
    releases = _candidates(connection, recipe_key)
    selected = tuple(
        release.id
        for release in releases
        if release.status in _CURRENT_REVIEWED
        and release.version.major == minimum.major
        and minimum <= release.version < maximum
    )
    if not selected:
        raise RecipeReleaseUnavailable("No reviewed releases in compatible range.")
    return selected


def recipe_release_dispatch_eligible(
    connection: Connection, *, recipe_key: str, release_id: UUID
) -> bool:
    """Verify signed identity and current recipe status, not other dispatch gates."""
    if not isinstance(recipe_key, str) or _KEY.fullmatch(recipe_key) is None:
        raise ValueError("Recipe family is invalid.")
    if not isinstance(release_id, UUID):
        raise ValueError("Recipe release ID is invalid.")
    releases = _candidates(connection, recipe_key)
    matching = [release for release in releases if release.id == release_id]
    if len(matching) != 1:
        return False
    if matching[0].status not in _CURRENT_REVIEWED:
        return False
    try:
        row = connection.execute(
            "SELECT control.recipe_release_dispatch_eligible(%s)", (release_id,)
        ).fetchone()
    except Error:
        raise RecipeReleaseUnavailable("Recipe dispatch status unavailable.") from None
    if row is None or type(row[0]) is not bool:
        raise RecipeReleaseUnavailable("Recipe dispatch status unavailable.")
    return row[0] and matching[0].delivery_mode != "proposal_only"


def get_reviewed_recipe_release(
    connection: Connection, *, recipe_key: str, release_id: UUID
) -> RecipeRelease:
    """Return one signed current release only while the denial gate permits dispatch."""
    if not recipe_release_dispatch_eligible(
        connection, recipe_key=recipe_key, release_id=release_id
    ):
        raise RecipeReleaseUnavailable("Recipe release is unavailable for dispatch.")
    matching = [item for item in _candidates(connection, recipe_key) if item.id == release_id]
    if len(matching) != 1 or matching[0].status != "REVIEWED":
        raise RecipeReleaseUnavailable("Recipe release is unavailable for dispatch.")
    return matching[0]
