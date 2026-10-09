"""Owner-bound pause and evidence-only weekly report reads."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from psycopg import Connection

from signal_core.database import _clean_transaction
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import hash_session_token


@dataclass(frozen=True)
class PauseResult:
    state: str
    epoch: int
    draining_observations: int
    durability: str


def set_site_paused(
    connection: Connection,
    *,
    session_token: str,
    site_id: UUID,
    recovery_generation: str,
    paused: bool,
) -> PauseResult:
    if not isinstance(site_id, UUID) or type(paused) is not bool:
        raise ValueError("An exact site and pause state are required.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.set_weekly_site_pause(%s,%s,%s,%s)",
            (
                hash_session_token(session_token),
                site_id,
                validate_recovery_generation(recovery_generation),
                paused,
            ),
        ).fetchone()
    if row is None or row[0] not in {"paused", "pause_cleared"}:
        raise PermissionError("Current owner authority is required.")
    if row[3] not in {"ACKNOWLEDGED", "AUTHORITY_DURABILITY_PENDING"}:
        raise RuntimeError("Pause durability state is unavailable.")
    return PauseResult(row[0], row[1], row[2], row[3])


def read_weekly_report(
    connection: Connection,
    *,
    session_token: str,
    site_id: UUID,
    recovery_generation: str,
    week_start: date | None,
) -> dict | None:
    if not isinstance(site_id, UUID) or week_start is not None and not isinstance(week_start, date):
        raise ValueError("An exact site and week are required.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT control.read_latest_weekly_skill_report(%s,%s,%s)"
            if week_start is None
            else "SELECT control.read_change_measurement_report(%s,%s,%s,%s)",
            (
                hash_session_token(session_token),
                site_id,
                validate_recovery_generation(recovery_generation),
                *((week_start,) if week_start is not None else ()),
            ),
        ).fetchone()
    return row[0] if row else None


def read_site_pause(
    connection: Connection, *, session_token: str, site_id: UUID, recovery_generation: str
) -> dict:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT control.read_owner_weekly_pause(%s,%s,%s)",
            (
                hash_session_token(session_token),
                site_id,
                validate_recovery_generation(recovery_generation),
            ),
        ).fetchone()
    if row is None or row[0] is None:
        raise PermissionError("Current owner authority is required.")
    return row[0]
