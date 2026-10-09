"""Accepted visibility proposals enter the existing owner-review recipe boundary."""

from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from signal_core.business_brain import _session
from signal_core.content_writer import ContentWriterRejected, ContentWriterUnavailable
from signal_core.database import _clean_transaction
from signal_core.technical_recipe_service import seal_technical_recipe_revision

REASONS = {
    "step_up_required": "Sign in again with MFA before preparing this change.",
    "proposal_unavailable": "Accept this grounded recommendation before preparing a change.",
    "facts_unavailable": "The recommendation needs current approved business facts.",
    "inputs_unavailable": "A supported static repository, reviewed recipe "
    "and page finding are required.",
    "worker_unavailable": "The repository build worker is unavailable.",
    "type_unavailable": "This schema type has no supported grounded recipe.",
    "available": "Choose approved facts and prepare the exact change for Inbox review.",
    "sealed": "The exact change is waiting for review in the Inbox.",
}


def structured_context(connection, *, session_token, generation, site_id, proposal_id, digest):
    token_hash, generation = _session(session_token, generation)
    with _clean_transaction(connection):
        result = connection.execute(
            "SELECT control.ai_visibility_structured_context(%s,%s,%s,%s,%s)",
            (token_hash, generation, site_id, proposal_id, bytes.fromhex(digest)),
        ).fetchone()[0]
    if result is None:
        raise ContentWriterUnavailable("owner_access_denied")
    return result


def recipe_availability(context, *, configured):
    state = context["state"]
    if state == "available":
        if context.get("sealed"):
            return {"state": "sealed", "reason": REASONS["sealed"], **context["sealed"]}
        if context["payload"]["schema_type"] not in {
            "Article",
            "FAQPage",
            "Organization",
            "Product",
        }:
            state = "type_unavailable"
        elif not all(context.get(k) for k in ("input", "extension_id", "recipe_release_id")):
            state = "inputs_unavailable"
        elif not configured:
            state = "worker_unavailable"
    return {"state": state, "reason": REASONS[state]}


def grounded_inputs(payload, fact_fields):
    """Only exact approved claims selected by reference can become recipe values."""
    kind = payload["schema_type"]
    required, optional = {
        "Article": ({"headline"}, set()),
        "FAQPage": ({"question", "answer"}, set()),
        "Organization": ({"name"}, {"description"}),
        "Product": ({"name"}, {"description"}),
    }.get(kind, (set(), set()))
    if (
        not required
        or not isinstance(fact_fields, dict)
        or not required <= fact_fields.keys()
        or not fact_fields.keys() <= required | optional
    ):
        raise ValueError("Choose the required approved facts.")
    claims = {ref: claim["text"] for claim in payload["claims"] for ref in claim["fact_ids"]}
    if any(ref not in payload["fact_ids"] or ref not in claims for ref in fact_fields.values()):
        raise ContentWriterRejected("facts_unavailable")
    values = {field: claims[ref] for field, ref in fact_fields.items()}
    if kind in {"Organization", "Product"}:
        return {"@type": kind, "fact_refs": fact_fields}
    if kind == "FAQPage":
        return {"@type": kind, "questions": [values]}
    return {"@type": kind, **values}


async def seal_visibility_recipe(
    api_connection,
    identity_connection,
    *,
    session_token,
    generation,
    site_id,
    proposal_id,
    digest,
    fact_fields,
    credential,
    github_transport,
    runner,
):
    common = dict(
        session_token=session_token,
        generation=generation,
        site_id=site_id,
        proposal_id=proposal_id,
        digest=digest,
    )
    context = structured_context(api_connection, **common)
    availability = recipe_availability(context, configured=True)
    if availability["state"] == "sealed":
        if context["sealed"]["fact_fields"] != fact_fields:
            raise ContentWriterRejected("revision_conflict")
        return {"state": "sealed", **context["sealed"]}
    if availability["state"] != "available":
        if availability["state"] == "step_up_required":
            raise ContentWriterUnavailable("owner_access_denied")
        raise ContentWriterRejected(availability["state"])
    structured_data = grounded_inputs(context["payload"], fact_fields)
    path = urlsplit(context["payload"]["page_url"]).path.lstrip("/")
    source_path = (path + "index.html") if not path or path.endswith("/") else path
    key = uuid5(NAMESPACE_URL, f"visibility-recipe:{site_id}:{proposal_id}:{digest}")
    sealed = await seal_technical_recipe_revision(
        identity_connection,
        api_connection,
        session_token=session_token,
        current_recovery_generation=generation,
        site_id=site_id,
        extension_id=UUID(context["extension_id"]),
        report_id=UUID(context["input"]["report_id"]),
        finding_id=UUID(context["input"]["finding_id"]),
        recipe_key="structured_data_grounded",
        recipe_release_id=UUID(context["recipe_release_id"]),
        idempotency_key=key,
        build_idempotency_key=uuid5(NAMESPACE_URL, f"visibility-build:{key}"),
        credential=credential,
        github_transport=github_transport,
        runner=runner,
        structured_data=structured_data,
        source_path=source_path,
        brain_connection=api_connection,
        visibility_proposal_id=proposal_id,
        visibility_proposal_digest=digest,
        visibility_fact_fields=fact_fields,
    )
    return {
        "state": "sealed",
        "revision_id": str(sealed.id),
        "revision_sha256": sealed.revision_sha256,
    }
