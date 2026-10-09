"""Short authenticated database transactions around the existing budgeted model."""

from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb

from signal_core.ask_signal import (
    ENTAILMENT_INSTRUCTIONS,
    INSTRUCTIONS,
    NO_RECORD,
    bounded_text,
    business_statement,
    entailment_id,
    entailment_packet,
    entailment_schema,
    grounded_sentences,
    memory_kind,
    output_schema,
    packet,
    rolling_summary,
    suggestions,
    validate_output,
)
from signal_core.authorization import AuthorizationDenied
from signal_core.business_brain import _session
from signal_core.database import _clean_transaction
from signal_core.decision_contracts import canonical_json
from signal_core.model_budget import ModelBudgetUnavailable, PostgresModelBudget
from signal_core.model_reasoning import BusinessBrainModelAdapter, MetadataDraftError


class AssistantUnavailable(RuntimeError):
    pass


class AssistantConflict(ValueError):
    pass


def database_call(
    connection, *, session_token, generation, site_id, name="store", action=None, args=None
):
    if name not in {"store", "records"}:
        raise ValueError("Invalid assistant port.")
    token, generation = _session(session_token, generation)
    values = (token, generation, site_id)
    if name == "store":
        values += (action, Jsonb(args or {}))
    with _clean_transaction(connection):
        result = connection.execute(
            f"SELECT control.assistant_{name}(" + ",".join(["%s"] * len(values)) + ")", values
        ).fetchone()[0]
    if result is None:
        raise AuthorizationDenied()
    if isinstance(result, dict) and result.get("state") in {"conflict", "limit", "invalid"}:
        raise AssistantConflict("The request conflicts or exceeds the conversation limit.")
    if action == "memory":
        while len(canonical_json(result)) > 250000:
            result["memories"].pop()
    return result


@dataclass(frozen=True, repr=False)
class AssistantBudget(PostgresModelBudget):
    def call(self, name, *args):
        if name not in {"read", "reserve", "dispatch", "finish"}:
            raise ValueError("Assistant cannot manage budget caps.")
        token, generation = _session(self.session_token, self.generation)
        with _clean_transaction(self.connection):
            result = self.connection.execute(
                f"SELECT control.assistant_budget_{name}("
                + ",".join(["%s"] * (3 + len(args)))
                + ")",
                (token, generation, self.site_id, *args),
            ).fetchone()[0]
        if result is None or result == "denied":
            raise ModelBudgetUnavailable("MODEL_BUDGET_ACCESS_DENIED")
        return result


def availability(connection, reasoner, common):
    account = AssistantBudget(connection, **common).call("read")
    if not isinstance(reasoner, BusinessBrainModelAdapter) or not reasoner.configured:
        return "model_unconfigured", "Ask Signal's model is not configured."
    if account["used_micros"] >= account["cap_micros"]:
        return "budget_exhausted", "The monthly model budget is exhausted."
    return "available", None


def read_assistant(connection, *, reasoner, **common):
    view = database_call(connection, **common, action="list")
    records = database_call(connection, **common, name="records")
    state, reason = availability(connection, reasoner, common)
    return {
        "availability": state,
        "reason": reason,
        "suggestions": suggestions(records),
        "conversations": view["conversations"],
    }


def add_memory(connection, *, request_id, kind, text, **common):
    if not bounded_text(text, 500) or kind != memory_kind(text):
        raise AssistantConflict("Only personal preferences and future context can be remembered.")
    return database_call(
        connection,
        **common,
        action="add_memory",
        args={
            "request_id": str(request_id),
            "kind": kind,
            "text": text,
        },
    )


def clean_retrieval(retrieval):
    forgotten = retrieval.pop("forgotten", [])

    def allowed(text):
        return not any(t in text for t in forgotten)

    retrieval["turns"] = [t for t in retrieval["turns"] if allowed(t["text"])]
    retrieval["memories"] = [m for m in retrieval["memories"] if allowed(m["text"])]
    if retrieval["summary"] and not allowed(retrieval["summary"]["text"]):
        retrieval["summary"] = None
    return retrieval


def fact_proposal(connection, *, request_id, text, **common):
    # The existing Business Brain propose port, never its approve port. A stable ID
    # binds crash/replay to this request; the message journal admits this once only.
    token, generation = _session(common["session_token"], common["generation"])
    fact_id = uuid5(UUID(str(request_id)), "ask-signal-proposed-fact")
    with _clean_transaction(connection):
        result = connection.execute(
            "SELECT control.business_brain_propose(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                token,
                generation,
                common["site_id"],
                fact_id,
                "claim",
                text.strip(),
                "owner_statement",
                None,
                None,
                True,
            ),
        ).fetchone()[0]
    if result != "proposed":
        raise AuthorizationDenied()
    return {
        "text": (
            "I proposed that business fact for owner review. "
            "It is not approved and was not stored as memory."
        ),
        "state": "answered",
        "citations": [],
        "remembered": [],
        "actions": [
            {
                "intent": "open_fact",
                "label": "Review the proposed fact",
                "href": "/approvals?fact=" + str(fact_id),
                "confirm": "Confirm the fact in the Inbox before Signal may assert it.",
            }
        ],
    }


async def send_message(connection, *, reasoner, conversation_id, request_id, text, **common):
    if not bounded_text(text, 2000):
        raise ValueError("Invalid message.")
    args = {"conversation_id": str(conversation_id), "request_id": str(request_id), "text": text}
    started = database_call(connection, **common, action="begin", args=args)
    if started["state"] == "replayed":
        return {k: started[k] for k in ("owner_message", "reply")}
    owner = started["role"] == "owner"
    if business_statement(text):
        if owner:
            reply = fact_proposal(connection, **common, request_id=request_id, text=text)
        else:
            reply = {
                "text": (
                    "Only the owner can propose business facts. Nothing was remembered or approved."
                ),
                "state": "no_record",
                "citations": [],
                "actions": [],
                "remembered": [],
            }
    else:
        reply = await answer(connection, reasoner, args, started, common, owner)
    retrieval = clean_retrieval(database_call(connection, **common, action="retrieve", args=args))
    summary = rolling_summary(retrieval, args["text"], reply)
    if summary is not None:
        database_call(
            connection,
            **common,
            action="summary",
            args={**args, "text": summary, "message_id": started["owner_message"]["message_id"]},
        )
    memory_ids = reply.pop("_memory_ids", [])
    memories = reply.pop("_memories", [])
    return database_call(
        connection,
        **common,
        action="finish",
        args={**args, "reply": reply, "memory_ids": memory_ids, "memories": memories},
    )


async def answer(connection, reasoner, args, started, common, owner):
    state, reason = availability(connection, reasoner, common)
    if state != "available":
        return {
            "text": reason,
            "state": "unavailable",
            "citations": [],
            "actions": [],
            "remembered": [],
        }
    records = database_call(connection, **common, name="records")
    retrieval = clean_retrieval(database_call(connection, **common, action="retrieve", args=args))
    context = packet(records, retrieval, args["text"], owner)
    budget = AssistantBudget(connection, **common)
    try:
        result = await replace(reasoner, budget=budget)._call(
            context,
            output_schema(),
            UUID(started["reply"]["message_id"]),
            INSTRUCTIONS,
            role="owner_answers",
        )
        candidates = grounded_sentences(result.output, context)
    except MetadataDraftError as error:
        if error.code == "MODEL_INPUT_TOO_LARGE":
            return {
                "text": (
                    "The available context exceeds the answer limit. Nothing was sent to the model."
                ),
                "state": "failed",
                "citations": [],
                "actions": [],
                "remembered": [],
            }
        if error.code == "MODEL_BUDGET_EXHAUSTED":
            return {
                "text": "The monthly model budget is exhausted.",
                "state": "unavailable",
                "citations": [],
                "actions": [],
                "remembered": [],
            }
        return {
            **started["reply"],
            "text": (
                "The model answer could not be confirmed. Its budget hold is retained; "
                "this request will not be sent again."
            ),
            "state": "outcome_unknown",
        }
    except (ValueError, OSError):
        return {
            **started["reply"],
            "text": (
                "The model answer could not be confirmed. Its budget hold is retained; "
                "this request will not be sent again."
            ),
            "state": "outcome_unknown",
        }
    entailment, notice, failure_state = None, None, None
    if candidates:
        try:
            checked = await replace(reasoner, budget=budget)._call(
                entailment_packet(candidates, context),
                entailment_schema(len(candidates)),
                entailment_id(started["reply"]["message_id"]),
                ENTAILMENT_INSTRUCTIONS,
                role="owner_answers",
            )
            entailment = checked.output
            # Reject duplicate/missing indexes before any prose is exposed.
            validate_output(result.output, context, entailed=entailment)
        except (MetadataDraftError, ValueError, OSError) as error:
            entailment = None
            if isinstance(error, MetadataDraftError) and error.code == "MODEL_BUDGET_EXHAUSTED":
                notice = "The monthly model budget is exhausted; prose could not be verified."
                failure_state = "unavailable"
            elif isinstance(error, MetadataDraftError) and error.code == "MODEL_INPUT_TOO_LARGE":
                notice = "The verification context exceeds the model limit; prose was not verified."
                failure_state = "failed"
            else:
                notice = (
                    "Prose could not be verified. Any unresolved budget hold is retained; "
                    "this request will not be sent again."
                )
                failure_state = "outcome_unknown"
    reply, memories = validate_output(
        result.output,
        context,
        entailed=entailment,
        maximum=1200 - len(notice) - 2 if notice else 1200,
    )
    if notice:
        if reply["text"] == NO_RECORD:
            full, _ = validate_output(result.output, context)
            if full["citations"]:
                reply = full
        # Keep complete evidence rather than truncate a claim or falsely report
        # no record. For a maximum-size excerpt the API state carries the failure.
        if len(reply["text"]) + len(notice) + 2 <= 1200:
            reply["text"] += "\n\n" + notice
        reply["state"] = failure_state
        memories = []
    current = clean_retrieval(database_call(connection, **common, action="retrieve", args=args))
    live_records = database_call(connection, **common, name="records")
    if canonical_json(current["memories"]) != canonical_json(retrieval["memories"]) or any(
        r not in live_records
        for r in records
        if any(c["kind"] == r["kind"] and c["id"] == r["id"] for c in reply["citations"])
    ):
        return {
            "text": (
                "Records or memory changed while answering. Please ask again with a new request."
            ),
            "state": "failed",
            "citations": [],
            "actions": [],
            "remembered": [],
        }
    facts = [
        t
        for t in result.output["business_facts"]
        if owner and bounded_text(t, 500) and t in args["text"]
    ]
    for index, text in enumerate(facts):
        proposed = fact_proposal(
            connection,
            **common,
            request_id=uuid5(UUID(args["request_id"]), f"business-fact-{index}"),
            text=text,
        )
        reply["actions"] = (reply["actions"] + proposed["actions"])[:3]
        if index == 0:
            if len(reply["text"]) + len(proposed["text"]) + 2 > 1200:
                reply["text"] = (notice + "\n\n" if notice else "") + proposed["text"]
                reply["citations"] = []
            else:
                reply["text"] += "\n\n" + proposed["text"]
        if not failure_state:
            reply["state"] = "answered"
    reply["_memories"] = []
    for index, memory in enumerate(memories):
        if any(t in memory["text"] or memory["text"] in t for t in facts):
            continue
        reply["_memories"].append(
            {
                "request_id": str(uuid5(UUID(args["request_id"]), f"memory-{index}")),
                **memory,
            }
        )
    reply["_memory_ids"] = [m["memory_id"] for m in retrieval["memories"]]
    if retrieval["summary"]:
        reply["_memory_ids"].append(retrieval["summary"]["memory_id"])
    return reply
