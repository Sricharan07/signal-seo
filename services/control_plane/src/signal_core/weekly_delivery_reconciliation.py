"""Read-only recovery of an exact journaled effect; never retry a write."""

import json
from hashlib import sha256
from uuid import UUID

import rfc8785

from signal_core.database import _clean_transaction
from signal_core.github_pr_delivery import (
    GitHubPrDeliveryUnavailable,
    _call_status,
    _read_operation,
    _reconcile_step,
    _status,
)
from signal_core.github_pr_extension import read_github_pr_extension
from signal_core.github_pr_patch import GitHubPrPatch
from signal_core.github_pr_provider import GitHubPrProvider, GitHubWriteEgressTransport
from signal_core.github_read_binding import read_github_read_binding
from signal_core.weekly_delivery_authority import WeeklyDeliveryConnection


async def reconcile_weekly_operation(
    connection: WeeklyDeliveryConnection,
    *,
    handle: str,
    operation_id: UUID,
    revision_id: UUID,
    extension_id: UUID,
    worker_id: UUID,
    credential,
    egress,
    openbao_transport=None,
):
    args = (connection.handle_hash, connection.site_id, connection.generation)
    with _clean_transaction(connection.connection):
        receipt = connection.connection.execute(
            "SELECT * FROM control.read_weekly_pr_execution(%s,%s,%s)", args
        ).fetchone()
    if receipt is None:
        raise GitHubPrDeliveryUnavailable("NO_COMMITTED_EXECUTION_RECEIPT")
    canonical = bytes(receipt[0])
    body = json.loads(canonical)
    if rfc8785.dumps(body) != canonical or sha256(canonical).digest() != bytes(receipt[1]):
        raise GitHubPrDeliveryUnavailable("EXECUTION_RECEIPT_INVALID")
    patch = GitHubPrPatch(
        path=body["path"],
        content=body["content"].encode("utf-8"),
        patch_sha256=body["patch_sha256"],
        tree_sha=body["tree_sha"],
        commit_sha=body["commit_sha"],
        commit_message=body["commit_message"],
        commit_date=body["commit_date"],
    )
    operation = _read_operation(connection, *args, operation_id)
    if operation.state not in {"dispatching", "outcome_unknown"}:
        return operation
    extension = read_github_pr_extension(
        connection,
        session_token=handle,
        current_recovery_generation=connection.generation,
        site_id=connection.site_id,
        extension_id=extension_id,
    )
    binding = read_github_read_binding(
        connection,
        session_token=handle,
        current_recovery_generation=connection.generation,
        site_id=connection.site_id,
        binding_id=extension.binding_id,
    )
    with _clean_transaction(connection):
        claim = connection.execute(
            "SELECT * FROM control.claim_github_pr_operation(%s,%s,%s,%s,%s)",
            (*args, operation_id, worker_id),
        ).fetchone()
    if claim is None or claim[3] != "claimed":
        raise GitHubPrDeliveryUnavailable("RECONCILIATION_LEASE_UNAVAILABLE")
    fence = claim[0]

    def read_guard():
        with _clean_transaction(connection.connection):
            lease = connection.connection.execute(
                "SELECT control.weekly_reconciliation_read_permit(%s,%s,%s,%s,%s,%s)",
                (*args, operation_id, worker_id, fence),
            ).fetchone()
        if lease is None or not lease[0]:
            raise GitHubPrDeliveryUnavailable("RECONCILIATION_DENIED")

    transport = GitHubWriteEgressTransport(
        egress,
        binding.target,
        lambda step: None,
        operation_id=operation_id,
        patch=patch,
        base_sha=body["base_sha"],
        base_tree_sha=body["base_tree_sha"],
        pr_body=body["pr_body"],
        read_guard=read_guard,
    )
    provider = GitHubPrProvider(
        credentials=await credential.credentials(transport=openbao_transport),
        target=binding.target,
        transport=transport,
    )
    await provider.acquire_token()
    result = await _reconcile_step(
        provider, operation.step, operation.branch_name, operation.base_sha, patch, body["pr_body"]
    )
    if result is None:
        raise GitHubPrDeliveryUnavailable("OUTCOME_UNKNOWN")
    evidence, pr = result
    _status(
        _call_status(
            connection,
            "reconcile_github_pr_step",
            *args,
            operation_id,
            worker_id,
            fence,
            operation.step,
            bytes.fromhex(evidence),
            pr.number if pr else None,
            pr.url if pr else None,
        ),
        "reconciled",
    )
    return _read_operation(connection, *args, operation_id)
