"""Evidence-first planning with an optional separately authorized research step."""

from uuid import uuid4

from signal_core.authorization import AuthorizationDenied
from signal_core.business_brain import _session
from signal_core.database import _clean_transaction
from signal_core.dataforseo_credentials import DataForSeoUnavailable
from signal_core.decision_contracts import canonical_json
from signal_core.seo_strategy import build_snapshot
from signal_core.shared_egress import ProviderEgressUnavailable


class StrategyUnavailable(RuntimeError):
    pass


class StrategyConflict(ValueError):
    pass


def strategy_call(connection, *, session_token, generation, site_id, action, args=()):
    if action not in {"sources", "record", "read", "decide", "record_ideas"}:
        raise ValueError("Invalid strategy action.")
    token_hash, generation = _session(session_token, generation)
    with _clean_transaction(connection):
        result = connection.execute(
            f"SELECT control.seo_strategy_{action}(" + ",".join(["%s"] * (3 + len(args))) + ")",
            (token_hash, generation, site_id, *args),
        ).fetchone()[0]
    if result is None:
        raise AuthorizationDenied()
    return result


def refresh_strategy(connection, *, session_token, generation, site_id):
    common = {"session_token": session_token, "generation": generation, "site_id": site_id}
    sources = strategy_call(connection, **common, action="sources")
    snapshot = build_snapshot(sources)
    result = strategy_call(
        connection, **common, action="record", args=(uuid4(), canonical_json(snapshot))
    )
    if result["state"] == "stale":
        raise StrategyConflict("Evidence changed; refresh before planning.")
    if result["state"] not in {"recorded", "replayed"}:
        raise StrategyUnavailable("Snapshot could not be recorded.")
    return result


async def rebuild_strategy(
    connection, *, session_token, generation, site_id, research=None, guard=lambda: None
):
    common = {"session_token": session_token, "generation": generation, "site_id": site_id}
    sources = strategy_call(connection, **common, action="sources")
    if research is not None:
        try:
            await research.run(sources, **common, guard=guard)
        except (DataForSeoUnavailable, ProviderEgressUnavailable):
            # Missing optional composition cannot erase the unpaid source snapshot.
            pass
    guard()
    # Read again so the immutable snapshot pins the newly recorded provider receipts.
    return refresh_strategy(connection, **common)


def decide_strategy(
    connection, *, session_token, generation, site_id, snapshot_id, item_id, decision
):
    if decision not in {"accepted", "dismissed"}:
        raise ValueError("Invalid strategy decision.")
    result = strategy_call(
        connection,
        session_token=session_token,
        generation=generation,
        site_id=site_id,
        action="decide",
        args=(snapshot_id, item_id, decision),
    )
    if result["state"] == "conflict":
        raise StrategyConflict("Strategy decision conflicts with the current plan.")
    if result["state"] == "unavailable":
        raise StrategyUnavailable("The current evidence or proposal target is unavailable.")
    return result
