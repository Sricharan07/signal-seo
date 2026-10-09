"""Inspect and revoke exact browser sessions without broad session authority."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg import Connection
from psycopg.errors import UniqueViolation

from signal_core.authorization import InvalidSession
from signal_core.crawl_urls import CrawlUrlRejected, normalize_crawl_url
from signal_core.database import _clean_transaction
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import session_token_hasher

BrowserSessionKind = Literal["identity", "tenant"]
_EVENT_ALLOCATION_ATTEMPTS = 3
_ROLES = frozenset({"viewer", "analyst", "editor", "approver", "admin", "owner"})
_AUTHENTICATION_LEVELS = frozenset({"primary", "mfa"})
_SITE_STATES = frozenset({"onboarding", "active"})
_OWNERSHIP_STATES = frozenset({"unverified", "verified", "reverification_required"})
_CURRENCY = re.compile(r"[A-Z]{3}")
_MAX_SITE_DIRECTORY = 100


@dataclass(frozen=True)
class CurrentTenantSession:
    tenant_id: UUID
    user_id: UUID
    role_key: str
    authentication_level: str
    expires_at: datetime
    session_version: int
    active_site_id: UUID | None


@dataclass(frozen=True)
class SelectedSiteContext:
    tenant_id: UUID
    user_id: UUID
    site_id: UUID
    session_version: int
    changed: bool


@dataclass(frozen=True)
class AuthorizedSite:
    id: UUID
    name: str
    primary_origin: str
    timezone: str
    reporting_currency: str
    state: str
    ownership_status: str


@dataclass(frozen=True)
class TenantSiteDirectory:
    tenant_id: UUID
    tenant_name: str
    sites: tuple[AuthorizedSite, ...]


class SiteSelectionDenied(Exception):
    """The current tenant session has no selectable grant for the requested site."""


class SessionContextConflict(Exception):
    """The browser attempted to replace a newer server-owned site context."""


def inspect_tenant_session(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
) -> CurrentTenantSession:
    """Return current tenant context only after all parent and membership checks."""
    token_hash = _session_hash(session_token)
    generation = validate_recovery_generation(current_recovery_generation)

    with _clean_transaction(connection):
        connection.execute(
            "SELECT set_config('signal.session_hash', %s, true)",
            (token_hash.hex(),),
        )
        row = connection.execute(
            "SELECT tenant_session.tenant_id, tenant_session.user_id, "
            "identity_session.authentication_level, tenant_session.expires_at, "
            "tenant_session.session_version, tenant_session.active_site_id "
            "FROM app.sessions tenant_session "
            "JOIN control.identity_sessions identity_session "
            "ON identity_session.id = tenant_session.identity_session_id "
            "AND identity_session.user_id = tenant_session.user_id "
            "JOIN control.users identity_user ON identity_user.id = tenant_session.user_id "
            "WHERE tenant_session.session_token_hash = %s "
            "AND tenant_session.revoked_at IS NULL "
            "AND tenant_session.expires_at > transaction_timestamp() "
            "AND identity_session.revoked_at IS NULL "
            "AND identity_session.expires_at > transaction_timestamp() "
            "AND identity_session.recovery_generation = %s "
            "AND tenant_session.auth_time = identity_session.auth_time "
            "AND tenant_session.mfa_level = identity_session.authentication_level "
            "AND identity_user.disabled_at IS NULL",
            (token_hash, generation),
        ).fetchone()
        if row is None:
            raise InvalidSession()
        (
            tenant_id,
            user_id,
            authentication_level,
            expires_at,
            session_version,
            active_site_id,
        ) = row
        connection.execute(
            "SELECT set_config('signal.tenant_id', %s, true)",
            (str(tenant_id),),
        )
        authority = connection.execute(
            "SELECT membership.role_key "
            "FROM app.memberships membership "
            "JOIN app.tenants tenant ON tenant.tenant_id = membership.tenant_id "
            "WHERE membership.tenant_id = %s AND membership.user_id = %s "
            "AND membership.state = 'active' AND tenant.lifecycle = 'active'",
            (tenant_id, user_id),
        ).fetchone()
        if authority is None:
            raise InvalidSession()

        selected_site_id = None
        if active_site_id is not None:
            connection.execute(
                "SELECT set_config('signal.site_id', %s, true)",
                (str(active_site_id),),
            )
            site_authority = connection.execute(
                "SELECT 1 FROM app.site_memberships site_membership "
                "JOIN app.sites site ON site.tenant_id = site_membership.tenant_id "
                "AND site.id = site_membership.site_id "
                "WHERE site_membership.tenant_id = %s "
                "AND site_membership.site_id = %s "
                "AND site_membership.user_id = %s "
                "AND site_membership.state = 'active' "
                "AND site_membership.permission_set = "
                '\'{"permissions":["site.snapshot.request"],"schema_version":1}\'::jsonb '
                "AND site.state <> 'archived'",
                (tenant_id, active_site_id, user_id),
            ).fetchone()
            if site_authority is not None:
                selected_site_id = active_site_id

        result = CurrentTenantSession(
            tenant_id=tenant_id,
            user_id=user_id,
            role_key=authority[0],
            authentication_level=authentication_level,
            expires_at=expires_at,
            session_version=session_version,
            active_site_id=selected_site_id,
        )
        _validate_projection(result)
        return result


def select_session_site(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    requested_site_id: object,
    expected_session_version: object,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> SelectedSiteContext:
    """Select one current site in the exact tenant session and append its evidence."""
    token_hash = _session_hash(session_token)
    generation = validate_recovery_generation(current_recovery_generation)
    if not isinstance(requested_site_id, UUID):
        raise SiteSelectionDenied()
    if (
        isinstance(expected_session_version, bool)
        or not isinstance(expected_session_version, int)
        or expected_session_version <= 0
    ):
        raise SessionContextConflict()
    if not callable(event_id_factory):
        raise ValueError("Site selection event factory must be callable.")

    for attempt_number in range(_EVENT_ALLOCATION_ATTEMPTS):
        event_id = _new_event_id(event_id_factory)
        try:
            with _clean_transaction(connection):
                connection.execute(
                    "SELECT set_config('signal.session_hash', %s, true)",
                    (token_hash.hex(),),
                )
                row = connection.execute(
                    "SELECT outcome, selected_tenant_id, selected_user_id, "
                    "selected_site_id, selected_session_version, selection_changed "
                    "FROM control.select_session_site(%s, %s, %s, %s, %s)",
                    (
                        token_hash,
                        generation,
                        requested_site_id,
                        expected_session_version,
                        event_id,
                    ),
                ).fetchone()
                if row is None or row[0] == "invalid_session":
                    raise InvalidSession()
                if row[0] == "selection_denied":
                    raise SiteSelectionDenied()
                if row[0] == "conflict":
                    raise SessionContextConflict()
                if row[0] != "selected":
                    raise RuntimeError("Site selection failed.")
                result = SelectedSiteContext(
                    tenant_id=row[1],
                    user_id=row[2],
                    site_id=row[3],
                    session_version=row[4],
                    changed=row[5],
                )
                _validate_selected_site_context(result)
                return result
        except UniqueViolation as error:
            if (
                error.diag.constraint_name != "session_site_context_events_pkey"
                or attempt_number == _EVENT_ALLOCATION_ATTEMPTS - 1
            ):
                raise RuntimeError("Site selection event allocation failed.") from None
    raise RuntimeError("Site selection event allocation failed.")


def list_tenant_sites(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
) -> TenantSiteDirectory:
    """List only current, non-archived sites authorized for one tenant session."""
    token_hash = _session_hash(session_token)
    generation = validate_recovery_generation(current_recovery_generation)

    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT outcome, directory_tenant_id, tenant_name, site_id, site_name, "
            "primary_origin, timezone, reporting_currency, site_state, ownership_status "
            "FROM control.list_tenant_sites(%s, %s)",
            (token_hash, generation),
        ).fetchall()
        if not rows or rows[0][0] != "listed":
            raise InvalidSession()
        if len(rows) > _MAX_SITE_DIRECTORY:
            raise RuntimeError("Site directory exceeded its bounded result size.")

        tenant_id = rows[0][1]
        tenant_name = _display_text(rows[0][2], maximum=200, label="tenant name")
        if not isinstance(tenant_id, UUID):
            raise RuntimeError("Site directory projection is invalid.")

        sites: list[AuthorizedSite] = []
        seen: set[UUID] = set()
        for row in rows:
            if row[0] != "listed" or row[1] != tenant_id or row[2] != tenant_name:
                raise RuntimeError("Site directory projection is invalid.")
            if row[3] is None:
                if len(rows) != 1:
                    raise RuntimeError("Site directory projection is invalid.")
                continue
            site = _authorized_site(row[3:])
            if site.id in seen:
                raise RuntimeError("Site directory projection is invalid.")
            seen.add(site.id)
            sites.append(site)

        sites.sort(key=lambda item: (item.name.casefold(), str(item.id)))
        return TenantSiteDirectory(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            sites=tuple(sites),
        )


def revoke_browser_session(
    connection: Connection,
    *,
    session_token: object,
    presented_session_kind: object,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> bool:
    """Revoke the exact parent identity session and audit the first transition."""
    token_hash = _session_hash(session_token)
    if presented_session_kind not in {"identity", "tenant"}:
        raise ValueError("Browser session kind is invalid.")
    if not callable(event_id_factory):
        raise ValueError("Session revocation event factory must be callable.")

    for attempt_number in range(_EVENT_ALLOCATION_ATTEMPTS):
        event_id = _new_event_id(event_id_factory)
        try:
            with _clean_transaction(connection):
                setting = (
                    "signal.identity_session_hash"
                    if presented_session_kind == "identity"
                    else "signal.session_hash"
                )
                connection.execute(
                    "SELECT set_config(%s, %s, true)",
                    (setting, token_hash.hex()),
                )
                revoked = connection.execute(
                    "SELECT control.revoke_browser_session(%s, %s, %s)",
                    (token_hash, presented_session_kind, event_id),
                ).fetchone()
                if revoked is None or not isinstance(revoked[0], bool):
                    raise RuntimeError("Session revocation failed.")
                return revoked[0]
        except UniqueViolation as error:
            if (
                error.diag.constraint_name != "platform_events_pkey"
                or attempt_number == _EVENT_ALLOCATION_ATTEMPTS - 1
            ):
                raise RuntimeError("Session revocation event allocation failed.") from None
    raise RuntimeError("Session revocation event allocation failed.")


_session_hash = session_token_hasher(InvalidSession, include_type_error=True)


def _new_event_id(factory: Callable[[], UUID]) -> UUID:
    try:
        value = factory()
    except (RuntimeError, StopIteration, TypeError):
        raise RuntimeError("Session revocation event allocation failed.") from None
    if not isinstance(value, UUID) or value.version != 4:
        raise RuntimeError("Session revocation event allocation failed.")
    return value


def _validate_projection(value: CurrentTenantSession) -> None:
    if (
        not isinstance(value.tenant_id, UUID)
        or not isinstance(value.user_id, UUID)
        or value.role_key not in _ROLES
        or value.authentication_level not in _AUTHENTICATION_LEVELS
        or not isinstance(value.expires_at, datetime)
        or value.expires_at.tzinfo is None
        or value.expires_at.utcoffset() is None
        or isinstance(value.session_version, bool)
        or not isinstance(value.session_version, int)
        or value.session_version <= 0
        or (value.active_site_id is not None and not isinstance(value.active_site_id, UUID))
    ):
        raise RuntimeError("Session inspection failed.")


def _validate_selected_site_context(value: SelectedSiteContext) -> None:
    if (
        not isinstance(value.tenant_id, UUID)
        or not isinstance(value.user_id, UUID)
        or not isinstance(value.site_id, UUID)
        or isinstance(value.session_version, bool)
        or not isinstance(value.session_version, int)
        or value.session_version <= 1
        or not isinstance(value.changed, bool)
    ):
        raise RuntimeError("Site selection failed.")


def _authorized_site(values: tuple[object, ...]) -> AuthorizedSite:
    site_id, name, primary_origin, timezone, currency, state, ownership_status = values
    if not isinstance(site_id, UUID):
        raise RuntimeError("Site directory projection is invalid.")
    name = _display_text(name, maximum=200, label="site name")
    primary_origin = _canonical_origin(primary_origin)
    timezone = _timezone(timezone)
    if not isinstance(currency, str) or _CURRENCY.fullmatch(currency) is None:
        raise RuntimeError("Site directory projection is invalid.")
    if state not in _SITE_STATES or ownership_status not in _OWNERSHIP_STATES:
        raise RuntimeError("Site directory projection is invalid.")
    return AuthorizedSite(
        id=site_id,
        name=name,
        primary_origin=primary_origin,
        timezone=timezone,
        reporting_currency=currency,
        state=state,
        ownership_status=ownership_status,
    )


def _display_text(value: object, *, maximum: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise RuntimeError(f"Site directory {label} is invalid.")
    return value


def _canonical_origin(value: object) -> str:
    try:
        normalized = normalize_crawl_url(value)
    except CrawlUrlRejected:
        raise RuntimeError("Site directory origin is invalid.") from None
    if value != normalized.origin:
        raise RuntimeError("Site directory origin is invalid.")
    return normalized.origin


def _timezone(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise RuntimeError("Site directory timezone is invalid.")
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise RuntimeError("Site directory timezone is invalid.") from None
    return value
