"""Synthetic supplement to the real PostgreSQL/shared-egress qualification."""

import hashlib

import httpx2
from signal_core.model_budget import ModelBudgetUnavailable
from signal_core.model_reasoning import OpenAIResponsesAdapter
from signal_core.shared_egress import ProviderEgressResponse


class Budget:
    def __init__(self, cap=25000000):
        self.cap = cap
        self.calls = {}
        self.dispatches = set()
        self.receipts = {}
        self.events = []

    @property
    def used(self):
        return sum(self.receipts.get(key, record[1]) for key, record in self.calls.items())

    def reserve(self, operation_id, body, role, model):
        amount = model.reservation(len(body), 8192)
        if operation_id in self.calls:
            raise ModelBudgetUnavailable("MODEL_OUTCOME_UNKNOWN")
        if self.used + amount > self.cap:
            raise ModelBudgetUnavailable("MODEL_BUDGET_EXHAUSTED")
        self.calls[operation_id] = (hashlib.sha256(body).digest(), amount, role, model)
        self.events.append("reserve")

    def dispatch(self, operation_id, body):
        assert self.calls[operation_id][0] == hashlib.sha256(body).digest()
        if operation_id in self.dispatches:
            raise ModelBudgetUnavailable("MODEL_BUDGET_DISPATCH_DENIED")
        self.dispatches.add(operation_id)
        self.events.append("dispatch")

    def finish(self, operation_id, *, model, result, response_sha256):
        assert operation_id in self.dispatches and len(response_sha256) == 64
        self.receipts[operation_id] = model.cost(result.usage)
        self.events.append("finish")


class MockEgress:
    def __init__(self, transport):
        assert isinstance(transport, httpx2.MockTransport)
        self.transport = transport

    def post_json(self, **kwargs):
        with httpx2.Client(transport=self.transport, trust_env=False) as client:
            response = client.post(
                kwargs["url"],
                content=kwargs["body"],
                headers={
                    "Authorization": kwargs["authorization"],
                    "Content-Type": "application/json",
                },
            )
        return ProviderEgressResponse(
            response.status_code,
            response.headers.get("content-type", "").split(";")[0],
            response.content,
        )


def metadata_adapter(credential, *, response_transport):
    return OpenAIResponsesAdapter(
        credential, egress=MockEgress(response_transport), budget=Budget()
    )
