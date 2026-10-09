"""Dashboard opt-in and operator composition without email-based authority."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from psycopg import Connection

from signal_core.database import _clean_transaction
from signal_core.identity_conditions import normalize_ascii_mailbox
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import hash_session_token
from signal_core.smtp_submission import SmtpConfiguration


def configure_smtp(connection: Connection, configuration: SmtpConfiguration) -> None:
    """Operator bootstrap only; no credential enters PostgreSQL."""
    with _clean_transaction(connection):
        connection.execute(
            "SELECT control.configure_email_smtp(%s,%s,%s,%s,%s,%s,%s)",
            (
                configuration.host,
                configuration.port,
                configuration.tls_mode,
                configuration.sender,
                configuration.dashboard_origin,
                configuration.daily_cap,
                configuration.sha256,
            ),
        )


@dataclass(frozen=True, repr=False)
class EmailNotifications:
    connection: Connection
    recovery_generation: str

    def preference(self, session_token: str, site_id: UUID) -> dict:
        with _clean_transaction(self.connection):
            row = self.connection.execute(
                "SELECT control.read_email_preference(%s,%s,%s)",
                (
                    hash_session_token(session_token),
                    site_id,
                    validate_recovery_generation(self.recovery_generation),
                ),
            ).fetchone()
        if not row or row[0] is None:
            raise PermissionError("EMAIL_AUTHORITY_DENIED")
        return row[0]

    def opt_in(
        self, session_token: str, site_id: UUID, *, enabled: bool, address: str | None
    ) -> str:
        if type(enabled) is not bool:
            raise ValueError("An exact notification preference is required.")
        normalized = normalize_ascii_mailbox(address) if enabled else None
        with _clean_transaction(self.connection):
            state = self.connection.execute(
                "SELECT control.set_email_preference(%s,%s,%s,%s,%s,%s)",
                (
                    hash_session_token(session_token),
                    site_id,
                    validate_recovery_generation(self.recovery_generation),
                    enabled,
                    normalized,
                    uuid4(),
                ),
            ).fetchone()[0]
        if state == "denied":
            raise PermissionError("EMAIL_AUTHORITY_DENIED")
        return state

    def queue_report(
        self, session_token: str, site_id: UUID, week_start: date, membership_id: UUID
    ) -> UUID | None:
        with _clean_transaction(self.connection):
            return self.connection.execute(
                "SELECT control.enqueue_email_report(%s,%s,%s,%s,%s,%s)",
                (
                    hash_session_token(session_token),
                    site_id,
                    validate_recovery_generation(self.recovery_generation),
                    week_start,
                    membership_id,
                    uuid4(),
                ),
            ).fetchone()[0]
