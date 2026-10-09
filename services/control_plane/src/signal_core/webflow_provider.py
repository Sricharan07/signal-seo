"""Closed Webflow v2 routes through the shared egress gateway, without retries."""

import time
from dataclasses import dataclass, field
from uuid import uuid4

from signal_core.egress_profiles import EgressProfile, WebflowScope
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.webflow import WebflowUnavailable, document

API_ORIGIN = "https://api.webflow.com"


@dataclass(frozen=True, repr=False)
class WebflowProvider:
    egress: SharedEgressProvider = field(repr=False)
    scope: WebflowScope = field(repr=False)

    def __post_init__(self):
        if (
            not isinstance(self.egress, SharedEgressProvider)
            or self.egress.purpose != "connector"
            or self.egress.policy.allowed_origins != (API_ORIGIN,)
            or self.egress.policy.max_redirects != 0
            or self.egress.policy.request_timeout_seconds > 5
            or self.egress.policy.max_body_bytes > 131072
        ):
            raise WebflowUnavailable("WEBFLOW_EGRESS_UNAVAILABLE")

    def read(self, path: str):
        return self._request("GET", path, b"", uuid4())

    def create_once(self, body: bytes, operation_id):
        return self._request(
            "POST", f"/v2/collections/{self.scope.collection_id}/items/insert", body, operation_id
        )

    def _request(self, method, path, body, operation_id):
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    response = self.egress.request_json(
                        method=method,
                        url=API_ORIGIN + path,
                        profile=EgressProfile.WEBFLOW,
                        authorization="Bearer " + self.scope.token,
                        body=body,
                        operation_id=operation_id,
                        timeout_seconds=5,
                        max_response_bytes=131072,
                        webflow_scope=self.scope,
                    )
                    break
                except ProviderEgressUnavailable as error:
                    # Only a definitive pre-dispatch deferral can be re-admitted.
                    if error.code != "EGRESS_DEFERRED" or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            if response.status_code not in ({200} if method == "GET" else {202}):
                raise WebflowUnavailable("WEBFLOW_RESPONSE_UNAVAILABLE")
            return document(response.body)
        except (ProviderEgressUnavailable, ValueError, RuntimeError):
            raise WebflowUnavailable(
                "WEBFLOW_OUTCOME_UNKNOWN" if method == "POST" else "WEBFLOW_READ_UNAVAILABLE"
            ) from None


def oauth_request(egress: SharedEgressProvider, payload: dict, *, revoke=False):
    origin = "https://webflow.com" if revoke else API_ORIGIN
    if (
        not isinstance(egress, SharedEgressProvider)
        or egress.purpose != "connector"
        or egress.policy.allowed_origins != (origin,)
        or egress.policy.max_redirects != 0
        or egress.policy.request_timeout_seconds > 5
        or egress.policy.max_body_bytes > 131072
    ):
        raise WebflowUnavailable("WEBFLOW_EGRESS_UNAVAILABLE")
    from signal_core.decision_contracts import canonical_json

    try:
        operation, deadline = uuid4(), time.monotonic() + 5
        while True:
            try:
                response = egress.request_json(
                    method="POST",
                    url=origin
                    + ("/oauth/revoke_authorization" if revoke else "/oauth/access_token"),
                    profile=EgressProfile.WEBFLOW_REVOKE if revoke else EgressProfile.WEBFLOW_OAUTH,
                    authorization=None,
                    body=canonical_json(payload),
                    operation_id=operation,
                    timeout_seconds=5,
                    max_response_bytes=4096 if revoke else 16384,
                )
                break
            except ProviderEgressUnavailable as error:
                if error.code != "EGRESS_DEFERRED" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        if response.status_code != 200:
            raise WebflowUnavailable("WEBFLOW_OAUTH_UNAVAILABLE")
        return document(response.body)
    except (ProviderEgressUnavailable, ValueError, RuntimeError):
        raise WebflowUnavailable("WEBFLOW_OAUTH_UNAVAILABLE") from None
