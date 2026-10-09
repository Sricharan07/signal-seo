"""Short, owner-scoped transactions around model I/O, with retained unknown holds."""

import hashlib
from dataclasses import asdict, dataclass

from psycopg.types.json import Jsonb

from signal_core.business_brain import _session
from signal_core.database import _clean_transaction
from signal_core.decision_contracts import canonical_json


class ModelBudgetUnavailable(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class PostgresModelBudget:
    connection: object
    session_token: str
    generation: str
    site_id: object

    def validate_scope(self, egress):
        from signal_core.shared_egress import SharedEgressProvider

        if not isinstance(egress, SharedEgressProvider):
            raise ModelBudgetUnavailable("MODEL_EGRESS_SCOPE_UNAVAILABLE")
        account = self.call("read")
        if (
            str(egress.run.site_id) != str(self.site_id)
            or str(egress.run.tenant_id) != account["tenant_id"]
        ):
            raise ModelBudgetUnavailable("MODEL_EGRESS_SCOPE_MISMATCH")

    def call(self, name, *args):
        if name not in {"read", "reserve", "dispatch", "finish", "set_cap"}:
            raise ValueError("Unknown model budget port.")
        session_hash, generation = _session(self.session_token, self.generation)
        with _clean_transaction(self.connection):
            row = self.connection.execute(
                f"SELECT control.model_budget_{name}(" + ",".join(["%s"] * (3 + len(args))) + ")",
                (session_hash, generation, self.site_id, *args),
            ).fetchone()
        result = row[0] if row else None
        if result is None or result == "denied":
            raise ModelBudgetUnavailable("MODEL_BUDGET_ACCESS_DENIED")
        return result

    def reserve(self, operation_id, body, role, model):
        result = self.call(
            "reserve",
            operation_id,
            hashlib.sha256(body).digest(),
            role,
            Jsonb({**asdict(model), "release": model.release}),
            model.reservation(len(body), 8192),
        )
        if result != "reserved":
            raise ModelBudgetUnavailable(
                {"exhausted": "MODEL_BUDGET_EXHAUSTED", "replayed": "MODEL_OUTCOME_UNKNOWN"}.get(
                    result, "MODEL_BUDGET_UNAVAILABLE"
                )
            )

    def dispatch(self, operation_id, body):
        if self.call("dispatch", operation_id, hashlib.sha256(body).digest()) != "admitted":
            raise ModelBudgetUnavailable("MODEL_BUDGET_DISPATCH_DENIED")

    def finish(self, operation_id, *, model, result, response_sha256):
        receipt = {
            "response_id": result.provider_response_id,
            "model": result.model_reported,
            "usage": asdict(result.usage),
            "output_sha256": hashlib.sha256(canonical_json(result.output)).hexdigest(),
        }
        if self.call(
            "finish",
            operation_id,
            model.cost(result.usage),
            bytes.fromhex(response_sha256),
            Jsonb(receipt),
        ) not in {"completed", "replayed"}:
            raise ModelBudgetUnavailable("MODEL_BUDGET_RECEIPT_UNAVAILABLE")
