"""Bounded weekly sample/daily collector over the existing current-owner egress."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from signal_core.database import _clean_transaction
from signal_core.egress_profiles import EgressProfile, PageSpeedScope
from signal_core.owner_connector_egress import OwnerConnectorContext
from signal_core.pagespeed import (
    MAX_RESPONSE_BYTES,
    TIMEOUT_SECONDS,
    PageSpeedRejected,
    pagespeed_url,
    parse_pagespeed,
)
from signal_core.pagespeed_credentials import OpenBaoPageSpeedCredentials
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider


class PageSpeedUnavailable(RuntimeError):
    def __init__(self, code="PSI_UNAVAILABLE"):
        self.code = code
        super().__init__(code)


def _call(connection, name, session_token, generation, site_id, *values):
    if not isinstance(site_id, UUID):
        raise PageSpeedUnavailable("PSI_ACCESS_DENIED")
    validate_recovery_generation(generation)
    try:
        token_hash = hash_session_token(session_token)
    except (InvalidOpaqueSessionToken, TypeError):
        raise PageSpeedUnavailable("PSI_ACCESS_DENIED") from None
    parameters = (token_hash, generation, site_id, *values)
    with _clean_transaction(connection):
        result = connection.execute(
            f"SELECT control.{name}(" + ",".join(["%s"] * len(parameters)) + ")", parameters
        ).fetchone()[0]
    if result == "denied" or isinstance(result, dict) and result.get("outcome") == "denied":
        raise PageSpeedUnavailable("PSI_ACCESS_DENIED")
    return result


def read_pagespeed(connection, *, session_token, current_recovery_generation, site_id):
    result = _call(
        connection, "read_pagespeed", session_token, current_recovery_generation, site_id
    )
    if not isinstance(result, dict) or result.get("outcome") != "found":
        raise PageSpeedUnavailable()
    return result


def schedule_pagespeed(connection, *, session_token, current_recovery_generation, site_id):
    result = _call(
        connection, "schedule_pagespeed_sample", session_token, current_recovery_generation, site_id
    )
    if not isinstance(result, dict) or result.get("outcome") != "scheduled":
        raise PageSpeedUnavailable()
    return result


async def collect_weekly_pagespeed(
    connection, *, egress: SharedEgressProvider, credentials: OpenBaoPageSpeedCredentials
) -> dict:
    """A daily tick seals <=4 responses; repeats never re-dispatch ambiguous requests.

    The caller supplies current owner authority, not a persisted session or grant.
    Deployment of an unattended scheduler is not enabled by this internal port.
    """
    if (
        not isinstance(egress, SharedEgressProvider)
        or egress.purpose != "connector"
        or not isinstance(egress.run, OwnerConnectorContext)
        or not isinstance(credentials, OpenBaoPageSpeedCredentials)
    ):
        raise ValueError("PageSpeed requires the current-owner shared egress and OpenBao boundary.")
    context = egress.run
    plan = await asyncio.to_thread(
        schedule_pagespeed,
        connection,
        session_token=context.session_token,
        current_recovery_generation=context.recovery_generation,
        site_id=context.site_id,
    )
    return await collect_pagespeed_plan(
        connection,
        egress=egress,
        credentials=credentials,
        plan=plan,
        authority_token=context.session_token,
        generation=context.recovery_generation,
        site_id=context.site_id,
    )


async def collect_pagespeed_plan(
    connection, *, egress, credentials, plan, authority_token, generation, site_id
):
    api_key = await credentials.api_key()
    outcomes = []
    for sample in plan["samples"][:4]:
        identity = UUID(sample["sample_id"])
        scope = PageSpeedScope(sample["verified_origin"], api_key)
        url = pagespeed_url(scope, sample["url"], sample["strategy"])
        response = None
        for attempt in range(6):
            try:
                response = await asyncio.to_thread(
                    egress.request_json,
                    method="GET",
                    url=url,
                    profile=EgressProfile.PAGESPEED,
                    authorization=None,
                    pagespeed_scope=scope,
                    operation_id=identity,
                    timeout_seconds=TIMEOUT_SECONDS,
                    max_response_bytes=MAX_RESPONSE_BYTES,
                )
                break
            except ProviderEgressUnavailable as error:
                if error.code == "EGRESS_DEFERRED" and attempt < 5:
                    await asyncio.sleep(1)
                    continue
                reason = error.code
                if reason == "EGRESS_RESPONSE_REJECTED":
                    recorded = await asyncio.to_thread(
                        _call,
                        connection,
                        "finish_pagespeed",
                        authority_token,
                        generation,
                        site_id,
                        identity,
                        None,
                        None,
                        "PSI_RESPONSE_REJECTED",
                    )
                    if recorded != "recorded":
                        raise PageSpeedUnavailable("PSI_EVIDENCE_UNAVAILABLE") from None
                    reason = "PSI_RESPONSE_REJECTED"
                outcomes.append(
                    {
                        "sample_id": str(identity),
                        "state": "deferred" if error.retryable else "unavailable",
                        "reason": reason,
                    }
                )
                break
            except Exception:
                raise PageSpeedUnavailable("PSI_TRANSPORT_UNAVAILABLE") from None
        if response is None:
            break
        reason = None
        observation = None
        if response.status_code == 429:
            reason = "PSI_RATE_LIMITED"
        elif response.status_code in {401, 403}:
            reason = "PSI_CREDENTIAL_REJECTED"
        elif response.status_code >= 500:
            reason = "PSI_PROVIDER_UNAVAILABLE"
        elif (
            response.status_code != 200
            or response.media_type != "application/json"
            or api_key is not None
            and api_key.encode() in response.body
        ):
            reason = "PSI_RESPONSE_REJECTED"
        else:
            try:
                observation = parse_pagespeed(
                    response.body,
                    url=sample["url"],
                    strategy=sample["strategy"],
                    evidence_id=identity,
                    fetched_at=datetime.now(UTC),
                )
            except PageSpeedRejected:
                reason = "PSI_RESPONSE_REJECTED"
        result = await asyncio.to_thread(
            _call,
            connection,
            "finish_pagespeed",
            authority_token,
            generation,
            site_id,
            identity,
            Jsonb(observation.projection()) if observation else None,
            observation.response_sha256 if observation else None,
            reason,
        )
        if result != "recorded":
            raise PageSpeedUnavailable("PSI_EVIDENCE_UNAVAILABLE")
        outcomes.append(
            {
                "sample_id": str(identity),
                "state": "unavailable" if reason else "observed",
                "reason": reason,
            }
        )
        if reason in {"PSI_RATE_LIMITED", "PSI_PROVIDER_UNAVAILABLE"}:
            break
    return {
        "week_start": plan["week_start"],
        "outcomes": outcomes,
        "credential_mode": "openbao_key" if api_key else "keyless_quota",
    }
