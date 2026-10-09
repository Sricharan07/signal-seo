"""Closed workload database ports, distinct from human identity sessions."""

import re
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import UUID

import rfc8785
from psycopg import Connection
from psycopg.errors import InsufficientPrivilege

from signal_core.database import _clean_transaction
from signal_core.github_pr_patch import GitHubPrPatch
from signal_core.session_tokens import hash_session_token

_FUNCTIONS = frozenset(
    {
        "read_github_read_binding",
        "read_github_base_risk",
        "record_github_binding_state",
        "read_github_pr_extension",
        "prepare_candidate_build",
        "dispatch_candidate_build",
        "finish_candidate_build",
        "read_candidate_build",
        "load_candidate_recipe_evidence",
        "seal_candidate_recipe_revision",
        "read_authenticated_candidate_recipe_inbox",
        "prepare_github_pr_operation",
        "acknowledge_github_pr_intent",
        "claim_github_pr_operation",
        "bind_github_pr_operation_tree",
        "github_pr_dispatch_permit",
        "begin_github_pr_step",
        "finish_github_pr_step",
        "reconcile_github_pr_step",
        "read_authenticated_github_pr_operations",
        "read_github_pr_operation_authorities",
        "begin_github_delivery_observation",
        "github_delivery_read_permit",
        "finish_github_delivery_observation",
        "read_authenticated_github_delivery_observations",
    }
)
_CALL = re.compile(r"^(SELECT (?:\* FROM |[a-z_, ]+ FROM )?control\.)([a-z_]+)(\(.*)$", re.S)


@dataclass(frozen=True)
class DispatchAuthority:
    kind: str
    id: UUID
    owner_user_id: UUID
    decision_channel: str | None


class WeeklyDeliveryConnection:
    """Adapt existing recipe/provider logic to explicitly scoped SQL workload ports.

    The handle is not an identity session. Only signal_workflow can use these
    ports; PostgreSQL binds every resource to one admitted cycle and finding.
    No owner decision, grant, binding mutation, or arbitrary SQL is exposed.
    """

    def __init__(self, connection: Connection, *, handle: str, site_id: UUID, generation: str):
        self.connection = connection
        self.handle_hash = hash_session_token(handle)
        self.site_id = site_id
        self.generation = generation
        self.autocommit = connection.autocommit
        self.info = connection.info

    def transaction(self):
        return self.connection.transaction()

    def execute(self, query: str, params=None):
        match = _CALL.fullmatch(query)
        if match is not None:
            name = match[2]
            if name not in _FUNCTIONS:
                raise PermissionError("Workload database function is not allowed.")
            if (
                params is None
                or len(params) < 3
                or params[0] != self.handle_hash
                or params[1] != self.site_id
                or params[2] != self.generation
            ):
                raise PermissionError("Workload scope differs from the admitted handle.")
            return self.connection.execute(f"{match[1]}weekly_{name}{match[3]}", params)
        if params is None and (
            query.startswith("SELECT NULLIF(current_setting('signal.tenant_id', true), '')")
            or query
            in {
                "SET LOCAL statement_timeout = '5s'",
                "SET LOCAL lock_timeout = '2s'",
                "SELECT pg_is_in_recovery()",
            }
        ):
            return self.connection.execute(query)
        raise PermissionError("Workload database statement is not allowed.")

    def dispatch_authority(self, *, revision_id: UUID, revision_sha256: str, operation_id: UUID):
        try:
            with _clean_transaction(self.connection):
                row = self.connection.execute(
                    "SELECT * FROM control.read_weekly_dispatch_authority(%s,%s,%s,%s,%s,%s)",
                    (
                        self.handle_hash,
                        self.site_id,
                        self.generation,
                        revision_id,
                        bytes.fromhex(revision_sha256),
                        operation_id,
                    ),
                ).fetchone()
        except InsufficientPrivilege:
            raise PermissionError("Exact workload scope is required.") from None
        if row is None:
            raise PermissionError("Current exact dispatch authority is unavailable.")
        if (row[0] == "owner_inbox" and row[3] not in {"dashboard", "slack", "telegram"}) or (
            row[0] == "standing_grant" and row[3] is not None
        ):
            raise PermissionError("Exact decision channel is unavailable.")
        return DispatchAuthority(row[0], row[1], row[2], row[3])

    def record_execution(
        self,
        *,
        operation_id: UUID,
        revision_sha256: str,
        base_sha: str,
        base_tree_sha: str,
        patch: GitHubPrPatch,
        body: str,
        authority: DispatchAuthority,
    ) -> None:
        canonical = rfc8785.dumps(
            {
                "schema_version": 1,
                "revision_sha256": revision_sha256,
                "base_sha": base_sha,
                "base_tree_sha": base_tree_sha,
                "path": patch.path,
                "content": patch.content.decode("utf-8"),
                "patch_sha256": patch.patch_sha256,
                "tree_sha": patch.tree_sha,
                "commit_sha": patch.commit_sha,
                "commit_message": patch.commit_message,
                "commit_date": patch.commit_date,
                "pr_body": body,
                "authority_kind": authority.kind,
                "authority_id": str(authority.id),
                "decision_channel": authority.decision_channel,
            }
        )
        with _clean_transaction(self.connection):
            result = self.connection.execute(
                "SELECT control.record_weekly_pr_execution(%s,%s,%s,%s,%s)",
                (self.handle_hash, self.site_id, self.generation, operation_id, canonical),
            ).fetchone()
        if result is None or result[0] != "recorded":
            raise PermissionError("Exact execution receipt was not committed.")


@dataclass(frozen=True)
class WeeklyWorkload:
    id: UUID
    revision_id: UUID
    operation_id: UUID
    gate_decision_id: UUID
    finding_id: UUID
    report_id: UUID
    recipe_release_id: UUID
    extension_id: UUID
    recipe_key: str
    revision_key: UUID
    build_key: UUID
    handle: str = field(repr=False)


def stable_identity(value: str) -> UUID:
    return UUID(bytes=sha256(value.encode("ascii")).digest()[:16], version=4)
