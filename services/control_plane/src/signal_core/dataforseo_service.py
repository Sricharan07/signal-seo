"""Durable reservation-before-I/O and owner-only optional provider setup."""

import base64
import hashlib
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.connector_framework import connector_row
from signal_core.database import _clean_transaction
from signal_core.dataforseo import (
    DOCUMENTED_COST_MICROS,
    MAX_RESPONSE_BYTES,
    DataForSeoQuery,
    parse_response,
    reported_cost,
)
from signal_core.dataforseo_credentials import (
    DataForSeoUnavailable,
    OpenBaoDataForSeoCredentials,
    validate_credential,
)
from signal_core.egress_profiles import EgressProfile
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider


def _one(connection, function, *args):
    return connector_row(connection, function, *args, transaction=_clean_transaction)


async def execute_call(
    connection: Connection,
    credentials: OpenBaoDataForSeoCredentials,
    egress: SharedEgressProvider,
    *,
    tenant_id: UUID,
    site_id: UUID,
    credential_generation: UUID,
    operation_id: UUID,
    query: DataForSeoQuery,
    estimate_micros: int | None = None,
    secret_options: dict | None = None,
) -> dict:
    body = query.body()
    if (
        not isinstance(egress, SharedEgressProvider)
        or egress.purpose != "connector"
        or (egress.run.tenant_id, egress.run.site_id) != (tenant_id, site_id)
    ):
        raise DataForSeoUnavailable("DATAFORSEO_EGRESS_REQUIRED")
    estimate = DOCUMENTED_COST_MICROS[query.kind] if estimate_micros is None else estimate_micros
    if type(estimate) is not int or not DOCUMENTED_COST_MICROS[query.kind] <= estimate <= 100000000:
        raise DataForSeoUnavailable("DATAFORSEO_ESTIMATE_INVALID")
    reservation = _one(
        connection,
        "reserve_dataforseo",
        tenant_id,
        site_id,
        operation_id,
        credential_generation,
        query.kind,
        query.endpoint,
        hashlib.sha256(body).digest(),
        Jsonb(asdict(query)),
        estimate,
    )[0]
    if reservation == "replay":
        return _one(connection, "dataforseo_call", tenant_id, site_id, operation_id)[0]
    if reservation != "reserved":
        return {
            "status": "unavailable",
            "failure_code": "DATAFORSEO_" + reservation.upper(),
            "result": None,
        }
    status, code, cost, digest, task, result = (
        "unknown",
        "DATAFORSEO_UNAVAILABLE",
        None,
        None,
        None,
        None,
    )
    try:
        try:
            scope = await credentials.scope(
                tenant_id, site_id, credential_generation, **(secret_options or {})
            )
        except DataForSeoUnavailable:
            code = "DATAFORSEO_SECRET_UNAVAILABLE"
            raise
        response = egress.request_json(
            method="POST",
            url=query.endpoint,
            profile=EgressProfile.DATAFORSEO,
            authorization=scope.authorization,
            dataforseo_scope=scope,
            body=body,
            operation_id=operation_id,
            timeout_seconds=30,
            max_response_bytes=MAX_RESPONSE_BYTES,
        )
        if response.status_code in {401, 403}:
            status, code = "unavailable", "DATAFORSEO_CREDENTIAL_REJECTED"
        elif response.status_code == 429 or response.status_code >= 500:
            status, code = "unavailable", "DATAFORSEO_UNAVAILABLE"
        elif response.status_code != 200 or response.media_type != "application/json":
            status, code = "rejected", "DATAFORSEO_RESPONSE_REJECTED"
        else:
            # A provider echo of either secret must not enter business rows or errors.
            raw = base64.b64decode(scope.authorization[6:])
            login, password = raw.split(b":", 1)
            if any(
                value in response.body
                for value in (login, password, scope.authorization.encode(), raw)
            ):
                raise DataForSeoUnavailable("DATAFORSEO_RESPONSE_REJECTED")
            cost = reported_cost(query, response.body)
            digest = hashlib.sha256(response.body).digest()
            parsed = parse_response(query, response.body)
            status, code, cost, digest, task, result = (
                "complete",
                None,
                parsed.cost_micros,
                parsed.response_sha256,
                parsed.task_id,
                parsed.data,
            )
    except ProviderEgressUnavailable:
        pass
    except DataForSeoUnavailable as error:
        if error.code == "DATAFORSEO_RESPONSE_REJECTED":
            status, code = "rejected", error.code
    except Exception:
        # Provider/transport exceptions may contain credentials; keep the hold and fixed code.
        status, code = "unknown", "DATAFORSEO_UNAVAILABLE"
    outcome = _one(
        connection,
        "record_dataforseo",
        tenant_id,
        site_id,
        operation_id,
        status,
        code,
        cost,
        digest,
        task,
        Jsonb(result) if result is not None else None,
    )[0]
    if outcome != "recorded":
        raise DataForSeoUnavailable("DATAFORSEO_RECEIPT_UNAVAILABLE")
    return _one(connection, "dataforseo_call", tenant_id, site_id, operation_id)[0]


@dataclass(frozen=True, repr=False)
class DataForSeoSettingsService:
    connection: Connection
    credentials: OpenBaoDataForSeoCredentials
    recovery_generation: str
    secret_options: dict = field(default_factory=dict, repr=False)
    execution_configured: bool = False

    def _read(self, token, site):
        value = _one(
            self.connection,
            "read_dataforseo",
            hash_session_token(token),
            self.recovery_generation,
            site,
        )[0]
        if value is None:
            raise DataForSeoUnavailable("DATAFORSEO_ACCESS_DENIED")
        return value

    async def read(self, token: str, site: UUID) -> dict:
        value = self._read(token, site)
        tenant = UUID(value.pop("tenant_id"))
        generation = value.pop("credential_generation")
        configured = generation is not None and value["availability"] != "unconfigured"
        if configured:
            try:
                await self.credentials.scope(tenant, site, UUID(generation), **self.secret_options)
            except DataForSeoUnavailable:
                value["availability"] = "secret_unavailable"
        value["credential_configured"] = configured
        value["execution_configured"] = self.execution_configured
        if value["availability"] == "configured":
            value["availability"] = (
                "available" if self.execution_configured else "execution_unavailable"
            )
        value["features"] = {
            key: "available" if value["availability"] == "available" else "unavailable"
            for key in ("competitor_gap", "search_volume", "competitor_backlinks")
        }
        return value

    def _configure(self, token, site, action, generation=None, cap=5000000):
        row = _one(
            self.connection,
            "configure_dataforseo",
            hash_session_token(token),
            self.recovery_generation,
            site,
            action,
            generation,
            cap,
            uuid4(),
        )
        if row[2] != "configured":
            raise DataForSeoUnavailable(
                "DATAFORSEO_ACCESS_DENIED" if row[2] == "denied" else "DATAFORSEO_SETTINGS_CONFLICT"
            )
        return row

    async def control(self, token: str, site: UUID, command) -> dict:
        action = command.operation
        if action == "credential":
            login, password = command.login.get_secret_value(), command.password.get_secret_value()
            validate_credential(login, password)
            generation = uuid4()
            tenant, old, _ = self._configure(token, site, "prepare", generation)
            if old is not None:
                await self.credentials.remove(tenant, site, old, **self.secret_options)
            await self.credentials.put(
                tenant, site, generation, login, password, **self.secret_options
            )
            try:
                self._configure(token, site, "activate", generation)
            except DataForSeoUnavailable:
                await self.credentials.remove(tenant, site, generation, **self.secret_options)
                raise
        elif action == "remove":
            tenant, old, _ = self._configure(token, site, "remove")
            if old is not None:
                await self.credentials.remove(tenant, site, old, **self.secret_options)
        elif action == "cap":
            self._configure(token, site, "cap", cap=command.cap_micros)
        else:
            raise DataForSeoUnavailable("DATAFORSEO_COMMAND_REJECTED")
        return await self.read(token, site)


class StrategyProviderConnection:
    """Translate only the paid reservation into the dual-budget authority port."""

    def __init__(self, connection, token, generation, site):
        self.connection = connection
        self.scope = (hash_session_token(token), generation, site)
        self.autocommit, self.info = connection.autocommit, connection.info

    def transaction(self):
        return self.connection.transaction()

    def execute(self, query, params=None):
        for source, target in (
            ("reserve_dataforseo", "reserve_strategy_dataforseo"),
            ("record_dataforseo", "record_strategy_dataforseo"),
            ("dataforseo_call", "strategy_dataforseo_call"),
        ):
            if query.startswith(f"SELECT * FROM control.{source}("):
                query = query.replace(f"{source}(", f"{target}(%s,%s,%s,")
                params = (*self.scope, *params)
                break
        return self.connection.execute(query, params)


@dataclass(frozen=True, repr=False)
class StrategyDataForSeoResearch:
    connection_factory: object
    credentials: OpenBaoDataForSeoCredentials
    egress_factory: object
    location_code: int
    language_code: str
    include_backlinks: bool = False
    secret_options: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        DataForSeoQuery("volume", "research", self.location_code, self.language_code).body()
        if type(self.include_backlinks) is not bool:
            raise ValueError("Explicit backlink configuration required.")

    async def run(self, sources, *, session_token, generation, site_id, guard=lambda: None):
        from signal_core.keyword_topics import build_topics

        topics = build_topics(sources)
        subjects = []
        for cluster in topics["clusters"]:
            subjects.extend([cluster["title"], *(idea["query"] for idea in cluster["ideas"])])
        if not subjects:
            subjects = [p["title"] for p in sources["content"]["records"] if p["title"]]
        # Keep original query text; model text can select a subject, never a route or cost.
        subjects = list(dict.fromkeys(subjects))[:5]
        if not subjects:
            return
        with self.connection_factory() as raw:
            connection = StrategyProviderConnection(raw, session_token, generation, site_id)
            guard()
            settings = _one(raw, "strategy_provider_settings", *connection.scope)[0]
            if settings is None:
                return
            tenant, credential = (
                UUID(settings["tenant_id"]),
                UUID(settings["credential_generation"]),
            )
            with self.egress_factory(session_token, site_id, generation) as egress:

                async def call(query):
                    guard()
                    digest = hashlib.sha256(
                        credential.bytes + settings["month"].encode() + query.body()
                    ).digest()[:16]
                    # A stable v4-shaped intent deduplicates owner/weekly refresh for the month.
                    operation = UUID(bytes=digest, version=4)
                    return await execute_call(
                        connection,
                        self.credentials,
                        egress,
                        tenant_id=tenant,
                        site_id=site_id,
                        credential_generation=credential,
                        operation_id=operation,
                        query=query,
                        secret_options=self.secret_options,
                    )

                domains = []
                for subject in subjects:
                    try:
                        queries = [
                            DataForSeoQuery(k, subject, self.location_code, self.language_code)
                            for k in ("volume", "serp")
                        ]
                        for query in queries:
                            query.body()
                    except DataForSeoUnavailable:
                        continue
                    for query in queries:
                        result = await call(query)
                        if result["status"] != "complete":
                            return
                        domains.extend(c["domain"] for c in result["result"].get("competitors", []))
                if self.include_backlinks:
                    own = urlsplit(
                        sources.get("internal_links", {}).get("site_origin", "")
                    ).hostname
                    for domain in list(dict.fromkeys(domains))[:2]:
                        if domain == own or re.fullmatch(r"[a-z0-9.-]{1,253}", domain) is None:
                            continue
                        if (await call(DataForSeoQuery("backlinks", domain)))[
                            "status"
                        ] != "complete":
                            return
