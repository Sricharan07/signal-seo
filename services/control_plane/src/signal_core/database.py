"""Transaction-local scope is defense in depth, not session authentication."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus


@dataclass(frozen=True)
class Scope:
    tenant_id: UUID
    site_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, UUID) or not isinstance(self.site_id, UUID):
            raise ValueError("Scope requires UUID identities from a trusted caller.")


@contextmanager
def _clean_transaction(connection: Connection) -> Iterator[Connection]:
    if not connection.autocommit or connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError(
            "Use an idle autocommit connection; nested scope transactions are forbidden."
        )
    residual_scope = connection.execute(
        "SELECT NULLIF(current_setting('signal.tenant_id', true), ''), "
        "NULLIF(current_setting('signal.site_id', true), ''), "
        "NULLIF(current_setting('signal.session_hash', true), ''), "
        "NULLIF(current_setting('signal.identity_session_hash', true), ''), "
        "NULLIF(current_setting('signal.oidc_state_hash', true), ''), "
        "NULLIF(current_setting('signal.oidc_browser_binding_hash', true), ''), "
        "NULLIF(current_setting('signal.invitation_identity_proof_hash', true), ''), "
        "NULLIF(current_setting('signal.actor_user_id', true), ''), "
        "NULLIF(current_setting('signal.membership_epoch', true), ''), "
        "NULLIF(current_setting('signal.site_authorization_epoch', true), '')"
    ).fetchone()
    if residual_scope != (None, None, None, None, None, None, None, None, None, None):
        raise ValueError("Connection has residual session scope; discard it rather than reuse it.")
    with connection.transaction():
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '2s'")
        if connection.execute("SELECT pg_is_in_recovery()").fetchone()[0]:
            raise RuntimeError("Authoritative transactions require the current primary.")
        yield connection


@contextmanager
def scoped_transaction(connection: Connection, scope: Scope) -> Iterator[Connection]:
    """The future authenticated backend must authorize membership before calling this."""
    with _clean_transaction(connection):
        connection.execute(
            "SELECT set_config('signal.tenant_id', %s, true)", (str(scope.tenant_id),)
        )
        connection.execute("SELECT set_config('signal.site_id', %s, true)", (str(scope.site_id),))
        yield connection
