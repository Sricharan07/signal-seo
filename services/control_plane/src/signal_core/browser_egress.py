"""Browser forward proxy capability using the existing durable shared gateway."""

import base64
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import uuid4

from signal_core.browser_policy import BrowserLimits, BrowserRejected, admit_browser_url
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import PROFILE_RULES, EgressProfile, profile_headers
from signal_core.shared_egress import (
    SharedEgressBlocked,
    SharedEgressDeferred,
    SharedEgressReceipt,
    SharedEgressRequest,
    execute_shared_egress,
)


@dataclass(repr=False)
class BrowserEgressGateway:
    admission_connection: object
    ingest_connection: object
    store: object
    run: object
    policy: object
    fetcher: object
    artifact_key: object
    limits: BrowserLimits
    admission_policy: OriginAdmissionPolicy = field(default_factory=OriginAdmissionPolicy)
    worker_key: str = "worker.browser-egress"
    operations: list = field(default_factory=list, init=False)
    robots_denials: list = field(default_factory=list, init=False)
    bytes: int = field(default=0, init=False)
    requests: int = field(default=0, init=False)
    deadline: float = field(init=False)
    lock: object = field(default_factory=threading.Lock, init=False)
    last_error: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.deadline = time.monotonic() + self.limits.seconds
        for origin in self.policy.allowed_origins:
            admit_browser_url(self.policy, origin)

    def forward(self, request: dict) -> dict:
        with self.lock:
            try:
                if set(request) != {"method", "url"} or request["method"] not in {"GET", "HEAD"}:
                    raise BrowserRejected("BROWSER_METHOD_DENIED")
                url = admit_browser_url(self.policy, request["url"])
                remaining = self.deadline - time.monotonic()
                if remaining < 0.25 or self.bytes >= self.limits.bytes or self.requests >= 64:
                    raise BrowserRejected("BROWSER_BUDGET_EXHAUSTED")
                self.requests += 1
                operation_id = uuid4()
                rules = PROFILE_RULES[EgressProfile.BROWSER_WORKER_READ]
                outbound = SharedEgressRequest(
                    "browser",
                    EgressHttpRequest(
                        request["method"],
                        url,
                        headers=profile_headers(
                            EgressProfile.BROWSER_WORKER_READ, request["method"], None
                        ),
                        accepted_media_types=rules.response_media_types,
                        max_response_bytes=min(
                            self.limits.bytes - self.bytes, self.policy.max_body_bytes
                        ),
                        timeout_seconds=min(5, remaining, self.policy.request_timeout_seconds),
                    ),
                    EgressProfile.BROWSER_WORKER_READ,
                )
                reserved_bytes = outbound.http.max_response_bytes
                self.bytes += reserved_bytes
                # Deferral has not dispatched. Each bounded retry rechecks authority.
                request_deadline = min(self.deadline, time.monotonic() + 4)
                while True:
                    remaining = self.deadline - time.monotonic()
                    if remaining < 0.25:
                        self.bytes -= reserved_bytes
                        raise BrowserRejected("BROWSER_TIME_EXHAUSTED")
                    outbound = replace(
                        outbound,
                        http=replace(
                            outbound.http,
                            timeout_seconds=min(outbound.http.timeout_seconds, remaining),
                        ),
                    )
                    result = execute_shared_egress(
                        self.admission_connection,
                        self.ingest_connection,
                        self.store,
                        self.run,
                        self.policy,
                        self.fetcher,
                        outbound,
                        operation_id=operation_id,
                        worker_key=self.worker_key,
                        admission_policy=self.admission_policy,
                        artifact_key=self.artifact_key,
                    )
                    if not isinstance(result, SharedEgressDeferred):
                        break
                    delay = max(0.01, (result.retry_at - datetime.now(UTC)).total_seconds())
                    if time.monotonic() + delay >= request_deadline:
                        self.bytes -= reserved_bytes
                        raise BrowserRejected("BROWSER_ADMISSION_DEFERRED")
                    time.sleep(delay)
                if isinstance(result, SharedEgressReceipt):
                    self.operations.append(operation_id)
                    if result.network_outcome == "fetched":
                        self.bytes -= reserved_bytes - result.response.decoded_bytes
                    if (
                        result.network_outcome == "fetched"
                        and result.response.http_status is not None
                        and 200 <= result.response.http_status < 300
                        and self.bytes <= self.limits.bytes
                        and time.monotonic() <= self.deadline
                    ):
                        return {
                            "status": result.response.http_status,
                            "media_type": result.response.media_type,
                            "body": base64.b64encode(result.response.body).decode("ascii"),
                        }
                elif isinstance(result, SharedEgressBlocked):
                    self.bytes -= reserved_bytes
                    if result.robots_snapshot_id not in self.robots_denials:
                        self.robots_denials.append(result.robots_snapshot_id)
                raise BrowserRejected("BROWSER_EGRESS_DENIED")
            except Exception as error:
                self.last_error = (
                    str(error) if isinstance(error, BrowserRejected) else type(error).__name__
                )
                # This capability never permits redirects or tunnels. State outages,
                # stale robots and rate deferrals all remain denied/incomplete.
                return {"status": 403, "media_type": "text/plain", "body": ""}
