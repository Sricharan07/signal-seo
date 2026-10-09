"""Durable read-only observation; provider status is never delivery certification."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import httpx2
import rfc8785
from anyio import to_thread
from psycopg import Connection

from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.database import _clean_transaction
from signal_core.github_delivery_provider import (
    GitHubDeliveryEgressTransport,
    GitHubDeliveryProvider,
    GitHubDeliveryUnavailable,
)
from signal_core.github_pr_delivery import _read_operation
from signal_core.github_pr_extension import read_github_pr_extension
from signal_core.github_read_binding import (
    GitHubBindingUnavailable,
    OpenBaoGitHubAppCredential,
    read_github_read_binding,
)
from signal_core.live_verification import (
    LiveVerificationUnavailable,
    SharedLiveVerifier,
    sealed_live_postcondition,
)
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token
from signal_core.shared_egress import SharedEgressProvider


class GitHubObservationUnavailable(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GitHubDeliveryObservation:
    attempt_id: UUID
    operation_id: UUID
    state: str
    receipt: dict | None
    receipt_sha256: str | None
    observed_at: datetime
    next_observe_at: datetime


def read_authenticated_github_delivery_observations(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: UUID,
    current_recovery_generation: object,
) -> tuple[GitHubDeliveryObservation, ...]:
    try:
        token_hash = hash_session_token(session_token)
    except (InvalidOpaqueSessionToken, TypeError):
        raise InvalidSession() from None
    generation = validate_recovery_generation(current_recovery_generation)
    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT * FROM control.read_authenticated_github_delivery_observations(%s,%s,%s)",
            (token_hash, requested_site_id, generation),
        ).fetchall()
    if rows and rows[0][-1] == "invalid_session":
        raise InvalidSession()
    if rows and rows[0][-1] == "authorization_denied":
        raise AuthorizationDenied()
    if not rows or rows[0][-1] not in {"found", "not_found"}:
        raise GitHubObservationUnavailable("OBSERVATION_READ_UNAVAILABLE")
    if rows[0][-1] == "not_found":
        return ()
    result = []
    for row in rows:
        receipt = None
        if row[2] is not None:
            canonical = bytes(row[2])
            receipt = json.loads(canonical)
            if (
                rfc8785.dumps(receipt) != canonical
                or hashlib.sha256(canonical).hexdigest() != row[3]
            ):
                raise GitHubObservationUnavailable("OBSERVATION_RECEIPT_INVALID")
        result.append(
            GitHubDeliveryObservation(row[0], row[1], row[4], receipt, row[3], row[5], row[6])
        )
    return tuple(result)


async def observe_github_delivery(
    connection: Connection,
    *,
    session_token: object,
    site_id: UUID,
    current_recovery_generation: object,
    operation_id: UUID,
    attempt_id: UUID,
    environment: str,
    trusted_deployment_actor_id: int,
    credential: OpenBaoGitHubAppCredential,
    github_egress: SharedEgressProvider,
    live_verifier: SharedLiveVerifier,
    openbao_transport: httpx2.AsyncBaseTransport | None = None,
) -> dict:
    if not all(isinstance(value, UUID) for value in (site_id, operation_id, attempt_id)):
        raise ValueError("A typed observation identity is required.")
    if attempt_id.version != 4:
        raise ValueError("Observation attempts require UUID4 identities.")
    if not isinstance(live_verifier, SharedLiveVerifier):
        raise GitHubObservationUnavailable("OBSERVATION_EGRESS_SCOPE_REJECTED")
    generation = validate_recovery_generation(current_recovery_generation)
    token_hash = hash_session_token(session_token)
    operation = _read_operation(connection, token_hash, site_id, generation, operation_id)
    if (
        operation.state != "opened"
        or operation.expected_commit_sha is None
        or operation.expected_tree_sha is None
    ):
        raise GitHubObservationUnavailable("PR_NOT_OPENED")
    if (
        not isinstance(github_egress, SharedEgressProvider)
        or github_egress.run.site_id != site_id
        or live_verifier.run.site_id != site_id
    ):
        raise GitHubObservationUnavailable("OBSERVATION_EGRESS_SCOPE_REJECTED")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.begin_github_delivery_observation(%s,%s,%s,%s,%s,%s,%s)",
            (
                token_hash,
                site_id,
                generation,
                operation_id,
                attempt_id,
                environment,
                trusted_deployment_actor_id,
            ),
        ).fetchone()
    if row is None:
        raise GitHubObservationUnavailable("OBSERVATION_UNAVAILABLE")
    if row[2] == "completed":
        return json.loads(bytes(row[1]))
    if row[2] != "dispatching":
        raise GitHubObservationUnavailable(str(row[2]))
    canonical = bytes(row[0])
    manifest = json.loads(canonical)
    site_origin = manifest.get("evidence", {}).get("site_origin")
    if manifest.get("work_type") in {"new_article", "content_refresh"}:
        with _clean_transaction(connection):
            content = connection.execute(
                "SELECT control.read_content_delivery_candidate(%s,%s,%s,%s)",
                (token_hash, site_id, generation, operation.revision_id),
            ).fetchone()
        if content is None or content[0] is None:
            raise GitHubObservationUnavailable("ARTICLE_OBSERVATION_UNAVAILABLE")
        site_origin = content[0]["site_origin"]
    contract = sealed_live_postcondition(
        canonical, operation.revision_sha256, site_origin=site_origin
    )
    extension = read_github_pr_extension(
        connection,
        session_token=session_token,
        site_id=site_id,
        current_recovery_generation=generation,
        extension_id=UUID(manifest["extension_id"]),
    )
    binding = read_github_read_binding(
        connection,
        session_token=session_token,
        site_id=site_id,
        current_recovery_generation=generation,
        binding_id=extension.binding_id,
    )

    def guard() -> None:
        with _clean_transaction(connection):
            permitted = connection.execute(
                "SELECT control.github_delivery_read_permit(%s,%s,%s,%s)",
                (token_hash, site_id, generation, attempt_id),
            ).fetchone()
        if permitted is None or permitted[0] != "permitted":
            raise GitHubObservationUnavailable("OBSERVATION_AUTHORITY_CHANGED")

    transport = GitHubDeliveryEgressTransport(
        github_egress,
        binding.target,
        pr_number=operation.pr_number,
        head_sha=operation.expected_commit_sha,
        environment=environment,
        authority_guard=guard,
    )
    live, live_operation_id = None, None
    provider_observation = {
        "stage": "pr_opened",
        "reason": "GITHUB_DELIVERY_UNAVAILABLE",
        "checks": None,
        "merged_sha": None,
        "merged_tree_sha": None,
        "merged_at": None,
        "deployment": None,
    }
    outcome, reason = "inconclusive", "GITHUB_DELIVERY_UNAVAILABLE"
    try:
        guard()
        credentials = await credential.credentials(transport=openbao_transport)
        client = GitHubDeliveryProvider(
            credentials=credentials,
            target=binding.target,
            repository_id=binding.repository_id,
            transport=transport,
        )
        provider_observation = await client.observe(
            expected_tree_sha=operation.expected_tree_sha,
            trusted_deployment_actor_id=trusted_deployment_actor_id,
            site_origin=site_origin,
        )
        reason = provider_observation["reason"]
        outcome = (
            "inconclusive"
            if reason.startswith("EC_") or reason == "CUSTOMER_PR_CLOSED_UNMERGED"
            else "not_yet_deployed"
        )
        if provider_observation["stage"] == "deployed":
            guard()
            result, live_operation_id = await to_thread.run_sync(
                lambda: live_verifier.verify(contract, site_id=site_id, operation_id=attempt_id)
            )
            live = result.document()
            outcome, reason = result.outcome, result.reason
            # Confirm that the deployment did not change during the independent GET.
            after = await client.observe(
                expected_tree_sha=operation.expected_tree_sha,
                trusted_deployment_actor_id=trusted_deployment_actor_id,
                site_origin=site_origin,
            )
            if (
                after["deployment"] != provider_observation["deployment"]
                or after["merged_sha"] != provider_observation["merged_sha"]
            ):
                provider_observation = after
                outcome, reason = "inconclusive", "EC_123_DEPLOYMENT_CHANGED_DURING_FETCH"
    except (
        GitHubDeliveryUnavailable,
        LiveVerificationUnavailable,
        GitHubBindingUnavailable,
    ) as error:
        outcome, reason = "inconclusive", getattr(error, "code", "LIVE_VERIFICATION_UNAVAILABLE")
    receipt = {
        "schema_version": 1,
        "site_id": str(site_id),
        "attempt_id": str(attempt_id),
        "operation_id": str(operation_id),
        "revision_sha256": operation.revision_sha256,
        "environment": environment,
        "deployment_actor_id": trusted_deployment_actor_id,
        "provider": provider_observation,
        "provider_evidence": transport.evidence,
        "live": live,
        "live_egress_operation_id": str(live_operation_id) if live_operation_id else None,
        "outcome": outcome,
        "reason": reason,
        "recovery_plan": contract.recovery_plan,
        "delivery_certified": False,
    }
    body = rfc8785.dumps(receipt)
    with _clean_transaction(connection):
        finished = connection.execute(
            "SELECT control.finish_github_delivery_observation(%s,%s,%s,%s,%s,%s)",
            (token_hash, site_id, generation, attempt_id, body, hashlib.sha256(body).digest()),
        ).fetchone()
    if finished is None or finished[0] != "completed":
        raise GitHubObservationUnavailable("OBSERVATION_RECEIPT_NOT_COMMITTED")
    return receipt
