"""Owner-controlled site creation under one exact tenant session."""

import hashlib
import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg import Connection
from psycopg.errors import UniqueViolation

from signal_core.authorization import InvalidSession
from signal_core.crawl_urls import CrawlUrlRejected, normalize_crawl_url
from signal_core.database import _clean_transaction
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import session_token_hasher

_ALLOCATION_ATTEMPTS = 3
_CURRENCY = re.compile(r"[A-Z]{3}")
_COLLISION_CONSTRAINTS = frozenset(
    {
        "sites_pkey",
        "site_memberships_pkey",
        "tenant_site_routes_pkey",
        "session_site_context_events_pkey",
        "site_onboarding_events_pkey",
        "site_onboarding_events_site_key",
    }
)


class InvalidSiteOnboarding(ValueError):
    """The proposed site metadata is not a canonical onboarding request."""


class SiteOnboardingDenied(Exception):
    """The current principal cannot create a site in this tenant."""


class SiteOnboardingConflict(Exception):
    """The request is stale or reuses an idempotency key for different input."""


class SiteLimitReached(Exception):
    """The tenant has reached the bounded active/onboarding site limit."""


@dataclass(frozen=True)
class OnboardedSite:
    tenant_id: UUID
    user_id: UUID
    site_id: UUID
    name: str
    primary_origin: str
    timezone: str
    reporting_currency: str
    session_version: int
    replayed: bool


def onboard_site(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    name: object,
    primary_origin: object,
    timezone: object,
    reporting_currency: object,
    expected_session_version: object,
    idempotency_key: object,
    site_id_factory: Callable[[], UUID] = uuid4,
    site_membership_id_factory: Callable[[], UUID] = uuid4,
    context_event_id_factory: Callable[[], UUID] = uuid4,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> OnboardedSite:
    """Create, grant, select, and audit one unverified site atomically."""
    token_hash = _session_hash(session_token)
    generation = validate_recovery_generation(current_recovery_generation)
    site_name = _site_name(name)
    origin = normalize_public_site_origin(primary_origin)
    site_timezone = _site_timezone(timezone)
    currency = _reporting_currency(reporting_currency)
    version = _session_version(expected_session_version)
    request_id = _request_id(idempotency_key)
    request_hash = _request_hash(site_name, origin, site_timezone, currency)
    factories = (
        site_id_factory,
        site_membership_id_factory,
        context_event_id_factory,
        event_id_factory,
    )
    if not all(callable(factory) for factory in factories):
        raise InvalidSiteOnboarding("Site identity factories must be callable.")

    for attempt_number in range(_ALLOCATION_ATTEMPTS):
        site_id = _new_uuid(site_id_factory)
        site_membership_id = _new_uuid(site_membership_id_factory)
        context_event_id = _new_uuid(context_event_id_factory)
        event_id = _new_uuid(event_id_factory)
        try:
            with _clean_transaction(connection):
                row = connection.execute(
                    "SELECT outcome, onboarded_tenant_id, onboarded_user_id, "
                    "onboarded_site_id, site_name, primary_origin, timezone, "
                    "reporting_currency, selected_session_version, request_replayed "
                    "FROM control.onboard_site(%s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s)",
                    (
                        token_hash,
                        generation,
                        site_id,
                        site_membership_id,
                        context_event_id,
                        event_id,
                        request_id,
                        request_hash,
                        site_name,
                        origin,
                        site_timezone,
                        currency,
                        version,
                        100,
                    ),
                ).fetchone()
                if row is None or row[0] == "invalid_session":
                    raise InvalidSession()
                if row[0] == "onboarding_denied":
                    raise SiteOnboardingDenied()
                if row[0] == "conflict":
                    raise SiteOnboardingConflict()
                if row[0] == "limit_reached":
                    raise SiteLimitReached()
                if row[0] != "onboarded":
                    raise RuntimeError("Site onboarding failed.")
                result = OnboardedSite(
                    tenant_id=row[1],
                    user_id=row[2],
                    site_id=row[3],
                    name=row[4],
                    primary_origin=row[5],
                    timezone=row[6],
                    reporting_currency=row[7],
                    session_version=row[8],
                    replayed=row[9],
                )
                _validate_result(result)
                return result
        except UniqueViolation as error:
            if (
                error.diag.constraint_name not in _COLLISION_CONSTRAINTS
                or attempt_number == _ALLOCATION_ATTEMPTS - 1
            ):
                raise RuntimeError("Site identity allocation failed.") from None
    raise RuntimeError("Site identity allocation failed.")


def normalize_public_site_origin(value: object) -> str:
    """Return one canonical HTTPS DNS origin suitable for later public proof."""
    if not isinstance(value, str):
        raise InvalidSiteOnboarding("Site origin is invalid.")
    try:
        normalized = normalize_crawl_url(value)
    except CrawlUrlRejected:
        raise InvalidSiteOnboarding("Site origin is invalid.") from None
    raw_without_fragment = value.partition("#")[0]
    if (
        normalized.scheme != "https"
        or value != normalized.origin
        or normalized.request_target != "/"
        or "?" in raw_without_fragment
        or "#" in value
        or "." not in normalized.host
    ):
        raise InvalidSiteOnboarding("Site origin must be one canonical HTTPS DNS origin.")
    try:
        ipaddress.ip_address(normalized.host)
    except ValueError:
        return normalized.origin
    raise InvalidSiteOnboarding("Site origin must use a DNS hostname.")


def _site_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise InvalidSiteOnboarding("Site name is invalid.")
    return value


def _site_timezone(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise InvalidSiteOnboarding("Site timezone is invalid.")
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise InvalidSiteOnboarding("Site timezone is invalid.") from None
    return value


def _reporting_currency(value: object) -> str:
    if not isinstance(value, str) or _CURRENCY.fullmatch(value) is None:
        raise InvalidSiteOnboarding("Reporting currency is invalid.")
    return value


def _session_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SiteOnboardingConflict()
    return value


def _request_id(value: object) -> UUID:
    if not isinstance(value, UUID) or value.version != 4:
        raise InvalidSiteOnboarding("Site onboarding idempotency key is invalid.")
    return value


def _request_hash(name: str, origin: str, timezone: str, currency: str) -> bytes:
    payload = (
        f"site_name={name}\n"
        f"primary_origin={origin}\n"
        f"timezone={timezone}\n"
        f"reporting_currency={currency}"
    )
    return hashlib.sha256(payload.encode("utf-8")).digest()


_session_hash = session_token_hasher(InvalidSession, include_type_error=True)


def _new_uuid(factory: Callable[[], UUID]) -> UUID:
    try:
        value = factory()
    except (RuntimeError, StopIteration, TypeError):
        raise RuntimeError("Site identity allocation failed.") from None
    if not isinstance(value, UUID) or value.version != 4:
        raise RuntimeError("Site identity allocation failed.")
    return value


def _validate_result(value: OnboardedSite) -> None:
    try:
        valid_fields = (
            _site_name(value.name) == value.name
            and normalize_public_site_origin(value.primary_origin) == value.primary_origin
            and _site_timezone(value.timezone) == value.timezone
            and _reporting_currency(value.reporting_currency) == value.reporting_currency
        )
    except (AttributeError, InvalidSiteOnboarding):
        valid_fields = False
    if (
        not isinstance(value.tenant_id, UUID)
        or not isinstance(value.user_id, UUID)
        or not isinstance(value.site_id, UUID)
        or not valid_fields
        or isinstance(value.session_version, bool)
        or not isinstance(value.session_version, int)
        or value.session_version <= 1
        or not isinstance(value.replayed, bool)
    ):
        raise RuntimeError("Site onboarding projection is invalid.")
