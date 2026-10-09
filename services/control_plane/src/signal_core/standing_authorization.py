"""Owner-granted exact recipe scope and serialized site-wide weekly allowance."""

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.database import _clean_transaction
from signal_core.recipe_releases import resolve_reviewed_recipe_range
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import hash_session_token

WORK_TYPES = frozenset(
    {"research_audit", "draft_patch", "metadata_pr", "content_refresh_pr", "new_article_pr"}
)
_PATH = re.compile(r"/[A-Za-z0-9_./-]{0,1023}")


class StandingAuthorizationUnavailable(Exception):
    """Current identity, site, or reviewed recipe scope does not permit the request."""


class StandingAuthorizationConflict(Exception):
    """Another current grant already occupies the site or an identity was reused."""


@dataclass(frozen=True)
class RecipeRange:
    key: str
    minimum_inclusive: str
    maximum_exclusive: str


@dataclass(frozen=True)
class StandingGrantRequest:
    site_id: UUID
    recipe_ranges: tuple[RecipeRange, ...]
    thresholds: dict[str, float]
    weekly_volume_caps: dict[str, int]
    weekly_total_cap: int
    weekly_spend_cents: int
    excluded_paths: tuple[str, ...]
    starts_at: datetime
    ends_at: datetime
    recovery_window_hours: int

    def __post_init__(self) -> None:
        if not isinstance(self.site_id, UUID) or not 1 <= len(self.recipe_ranges) <= 16:
            raise ValueError("Standing grant scope is invalid.")
        if not all(isinstance(item, RecipeRange) for item in self.recipe_ranges):
            raise ValueError("Standing grant recipe range is invalid.")
        types = set(self.thresholds)
        if not types or not types <= WORK_TYPES or types != set(self.weekly_volume_caps):
            raise ValueError("Standing grant work types are invalid.")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
            for value in self.thresholds.values()
        ):
            raise ValueError("Standing grant threshold is invalid.")
        if (
            type(self.weekly_total_cap) is not int
            or not 1 <= self.weekly_total_cap <= 1000
            or type(self.weekly_spend_cents) is not int
            or not 0 <= self.weekly_spend_cents <= 100_000_000
            or any(
                type(value) is not int or not 1 <= value <= self.weekly_total_cap
                for value in self.weekly_volume_caps.values()
            )
        ):
            raise ValueError("Standing grant caps are invalid.")
        if (
            not isinstance(self.excluded_paths, tuple)
            or len(self.excluded_paths) > 64
            or len(set(self.excluded_paths)) != len(self.excluded_paths)
            or any(
                not isinstance(path, str)
                or _PATH.fullmatch(path) is None
                or ".." in path
                or "//" in path
                for path in self.excluded_paths
            )
        ):
            raise ValueError("Standing grant excluded paths are invalid.")
        if (
            not isinstance(self.starts_at, datetime)
            or not isinstance(self.ends_at, datetime)
            or self.starts_at.tzinfo is None
            or self.ends_at.tzinfo is None
            or self.ends_at <= self.starts_at
            or type(self.recovery_window_hours) is not int
            or not 1 <= self.recovery_window_hours <= 720
        ):
            raise ValueError("Standing grant dates or recovery window are invalid.")


@dataclass(frozen=True)
class StandingGrant:
    id: UUID
    recipe_release_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class RevokedGrant:
    restriction_event_id: UUID
    durability: str


@dataclass(frozen=True)
class StandingGrantView:
    state: str
    grant_id: UUID | None
    recipe_release_ids: tuple[UUID, ...]
    work_types: tuple[str, ...]
    thresholds: dict[str, float]
    weekly_volume_caps: dict[str, int]
    weekly_total_cap: int | None
    weekly_spend_cents: int | None
    excluded_paths: tuple[str, ...]
    starts_at: datetime | None
    ends_at: datetime | None
    recovery_window_hours: int | None
    restriction_event_id: UUID | None
    durability: str | None


def grant_standing_authorization(
    connection: Connection,
    *,
    session_token: str,
    current_recovery_generation: str,
    request: StandingGrantRequest,
    grant_id: UUID | None = None,
) -> StandingGrant:
    """Verify signed reviewed releases and commit their exact IDs with the owner grant."""
    if not isinstance(request, StandingGrantRequest):
        raise ValueError("Standing grant request is invalid.")
    generation = validate_recovery_generation(current_recovery_generation)
    token_hash = hash_session_token(session_token)
    new_id = grant_id or uuid4()
    if not isinstance(new_id, UUID):
        raise ValueError("Standing grant ID is invalid.")
    with _clean_transaction(connection):
        releases: list[UUID] = []
        for requested_range in request.recipe_ranges:
            releases.extend(
                resolve_reviewed_recipe_range(
                    connection,
                    recipe_key=requested_range.key,
                    minimum_inclusive=requested_range.minimum_inclusive,
                    maximum_exclusive=requested_range.maximum_exclusive,
                )
            )
        release_ids = tuple(dict.fromkeys(releases))
        if len(release_ids) > 64:
            raise ValueError("Standing grant contains too many releases.")
        row = connection.execute(
            "SELECT control.grant_standing_authorization(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s)",
            (
                token_hash,
                request.site_id,
                generation,
                new_id,
                list(release_ids),
                sorted(request.thresholds),
                Jsonb(request.thresholds),
                Jsonb(request.weekly_volume_caps),
                request.weekly_total_cap,
                request.weekly_spend_cents,
                list(request.excluded_paths),
                request.starts_at,
                request.ends_at,
                request.recovery_window_hours,
            ),
        ).fetchone()
        outcome = row[0] if row else None
        if outcome == "grant_exists":
            raise StandingAuthorizationConflict("A current site grant already exists.")
        if outcome != "granted":
            raise StandingAuthorizationUnavailable(str(outcome or "grant_unavailable"))
    return StandingGrant(new_id, release_ids)


def revoke_standing_authorization(
    connection: Connection,
    *,
    session_token: str,
    current_recovery_generation: str,
    site_id: UUID,
    grant_id: UUID,
    event_id: UUID | None = None,
) -> RevokedGrant:
    if not isinstance(site_id, UUID) or not isinstance(grant_id, UUID):
        raise ValueError("Standing grant identity is invalid.")
    generation = validate_recovery_generation(current_recovery_generation)
    token_hash = hash_session_token(session_token)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.revoke_standing_authorization(%s,%s,%s,%s,%s)",
            (token_hash, site_id, generation, grant_id, event_id or uuid4()),
        ).fetchone()
        if row is None or row[0] != "revoked" or not isinstance(row[1], UUID):
            raise StandingAuthorizationUnavailable(str(row[0] if row else "grant_unavailable"))
        if row[2] not in {"ACKNOWLEDGED", "AUTHORITY_DURABILITY_PENDING"}:
            raise StandingAuthorizationUnavailable("Durability state unavailable.")
        return RevokedGrant(row[1], row[2])


def read_standing_authorization(
    connection: Connection,
    *,
    session_token: str,
    current_recovery_generation: str,
    site_id: UUID,
) -> StandingGrantView:
    if not isinstance(site_id, UUID):
        raise ValueError("Standing grant site is invalid.")
    generation = validate_recovery_generation(current_recovery_generation)
    token_hash = hash_session_token(session_token)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.read_standing_authorization(%s,%s,%s)",
            (token_hash, site_id, generation),
        ).fetchone()
        if row is None or row[0] not in {
            "no_grant",
            "active",
            "revoked",
            "not_started",
            "expired",
            "recovery_stale",
        }:
            raise StandingAuthorizationUnavailable(str(row[0] if row else "grant_unavailable"))
        if row[0] == "no_grant":
            return StandingGrantView(
                "no_grant", None, (), (), {}, {}, None, None, (), None, None, None, None, None
            )
        return StandingGrantView(
            row[0],
            row[1],
            tuple(row[2]),
            tuple(row[3]),
            {key: float(value) for key, value in row[4].items()},
            {key: int(value) for key, value in row[5].items()},
            row[6],
            row[7],
            tuple(row[8]),
            row[9],
            row[10],
            row[11],
            row[12],
            row[13],
        )
