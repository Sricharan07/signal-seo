"""Read and decide immutable technical-recipe candidate revisions."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg import Connection

from signal_core.authorization import AuthorizationDenied, InvalidSession, validated_human_inputs
from signal_core.database import _clean_transaction
from signal_core.proposals import (
    ApprovalDecisionConflict,
    ApprovalNotFound,
    ApprovalPermissionDenied,
    ApprovalRevisionConflict,
)

CandidateReviewDecision = Literal["approved", "rejected", "changes_requested"]
CandidateReviewStatus = Literal[
    "pending", "approved", "rejected", "changes_requested", "superseded", "stale_base"
]
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DECISIONS = frozenset({"approved", "rejected", "changes_requested"})


@dataclass(frozen=True)
class CandidateRecipeInboxItem:
    revision_id: UUID
    revision_sha256: str
    manifest: dict[str, object]
    sealed_at: datetime
    recipe_release_id: UUID
    release_content_hash: str
    base_sha: str
    patch_sha256: str
    review_status: CandidateReviewStatus
    decision_id: UUID | None
    decision: CandidateReviewDecision | None
    decided_by_user_id: UUID | None
    decision_channel: str | None
    decided_at: datetime | None
    reused: bool = False


def read_authenticated_candidate_recipe_inbox(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
) -> tuple[CandidateRecipeInboxItem, ...]:
    """Read a bounded, server-authorized projection of immutable revisions."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT * FROM control.read_authenticated_candidate_recipe_inbox(%s, %s, %s)",
            (token_hash, requested_site_id, current_recovery_generation),
        ).fetchall()
    if not rows:
        raise RuntimeError("Candidate inbox read failed.")
    outcome = rows[0][14]
    _raise_outcome(outcome)
    if outcome == "not_found":
        return ()
    if len(rows) > 50 or any(row[14] != "found" for row in rows):
        raise RuntimeError("Candidate inbox read failed.")
    return tuple(_item_from_row(row, outcome="found") for row in rows)


def decide_authenticated_candidate_recipe_revision(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    revision_id: object,
    expected_revision_sha256: object,
    decision_id: object,
    decision: object,
) -> CandidateRecipeInboxItem:
    """Record one immutable owner decision; no repository operation is dispatched."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if not isinstance(revision_id, UUID) or not isinstance(decision_id, UUID):
        raise ApprovalNotFound()
    if (
        not isinstance(expected_revision_sha256, str)
        or _SHA256.fullmatch(expected_revision_sha256) is None
    ):
        raise ApprovalRevisionConflict()
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise ValueError("The candidate review decision is invalid.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.decide_authenticated_candidate_recipe_revision("
            "%s, %s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                revision_id,
                bytes.fromhex(expected_revision_sha256),
                decision_id,
                decision,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Candidate review decision failed.")
    _raise_outcome(row[15])
    if row[15] != "decided":
        raise RuntimeError("Candidate review decision failed.")
    return _item_from_row(row, outcome="decided")


def _raise_outcome(outcome: object) -> None:
    if outcome == "invalid_session":
        raise InvalidSession()
    if outcome == "authorization_denied":
        raise AuthorizationDenied()
    if outcome in {"approval_permission_denied", "step_up_required"}:
        raise ApprovalPermissionDenied()
    if outcome == "approval_not_found":
        raise ApprovalNotFound()
    if outcome == "stale_revision":
        raise ApprovalRevisionConflict()
    if outcome == "decision_conflict":
        raise ApprovalDecisionConflict()


def _item_from_row(row: object, *, outcome: str) -> CandidateRecipeInboxItem:
    try:
        values = row[:15]
        item = CandidateRecipeInboxItem(
            revision_id=values[0],
            revision_sha256=values[1],
            manifest=values[2],
            sealed_at=values[3],
            recipe_release_id=values[4],
            release_content_hash=values[5],
            base_sha=values[6],
            patch_sha256=values[7],
            review_status=values[8],
            decision_id=values[9],
            decision=values[10],
            decided_by_user_id=values[11],
            decision_channel=values[12],
            decided_at=values[13],
            reused=bool(row[14]) if outcome == "decided" else False,
        )
    except (IndexError, TypeError):
        raise RuntimeError("Candidate inbox projection is invalid.") from None
    if (
        not isinstance(item.revision_id, UUID)
        or _SHA256.fullmatch(item.revision_sha256) is None
        or not isinstance(item.manifest, dict)
        or not isinstance(item.sealed_at, datetime)
        or not isinstance(item.recipe_release_id, UUID)
        or _SHA256.fullmatch(item.release_content_hash) is None
        or not isinstance(item.base_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", item.base_sha)
        or _SHA256.fullmatch(item.patch_sha256) is None
        or item.review_status
        not in {"pending", "approved", "rejected", "changes_requested", "superseded", "stale_base"}
    ):
        raise RuntimeError("Candidate inbox projection is invalid.")
    if item.review_status in _DECISIONS:
        if not (
            isinstance(item.decision_id, UUID)
            and item.decision == item.review_status
            and isinstance(item.decided_by_user_id, UUID)
            and item.decision_channel in {"dashboard", "slack", "telegram"}
            and isinstance(item.decided_at, datetime)
        ):
            raise RuntimeError("Candidate inbox decision projection is invalid.")
    elif any(
        value is not None
        for value in (
            item.decision_id,
            item.decision,
            item.decided_by_user_id,
            item.decision_channel,
            item.decided_at,
        )
    ):
        raise RuntimeError("Candidate inbox decision projection is invalid.")
    return item
