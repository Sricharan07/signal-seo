"""Narrow workload adapters for existing import, extraction and proposal logic."""

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from signal_core.bing_binding import import_bing_observation
from signal_core.brand_documents import read_brand_document
from signal_core.business_brain import FactProvenance
from signal_core.business_brain_extraction import BusinessBrainExtractor
from signal_core.content_writer import validate_brief
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import PROFILE_RULES, EgressProfile
from signal_core.ga4_binding import Ga4Service
from signal_core.gsc_binding import import_gsc_analytics
from signal_core.owner_connector_egress import OwnerConnectorContext, WeeklyPageSpeedContext
from signal_core.pagespeed_collection import collect_pagespeed_plan
from signal_core.pagespeed_credentials import PageSpeedCredentialUnavailable
from signal_core.seo_strategy_service import rebuild_strategy
from signal_core.shared_egress import SharedEgressProvider
from signal_core.weekly_skills import SkillPermit, SkillResult

_CALL = re.compile(
    r"^(SELECT (?:\* FROM |[a-z_, ]+ FROM )?control\.)([a-z_][a-z0-9_]*)(\(.*)$", re.S
)
_OWNER_SHAPED = frozenset(
    {
        "business_brain_read",
        "business_brain_page_source",
        "business_brain_begin_extraction",
        "business_brain_finish_extraction",
        "read_brand_document_artifact",
        "seo_strategy_sources",
        "seo_strategy_record",
        "seo_strategy_read",
        "content_writer_inventory",
        "content_writer_read",
        "content_writer_create_brief",
        "ga4_owner_binding",
        "record_ga4_owner_import",
        "ga4_owner_reauth",
        "model_budget_read",
        "model_budget_reserve",
        "model_budget_dispatch",
        "model_budget_finish",
        "finish_pagespeed",
    }
)
_IMPORT = frozenset(
    {
        "current_gsc_binding",
        "record_gsc_import_generation",
        "mark_gsc_reauth_required",
        "current_bing_binding",
        "record_bing_import_generation",
        "record_bing_page_import_generation",
        "mark_bing_reauth_required",
    }
)
_LINK_PORTS = frozenset(
    {
        "read_github_read_binding",
        "read_github_base_risk",
        "record_github_binding_state",
        "read_github_pr_extension",
        "prepare_candidate_build",
        "dispatch_candidate_build",
        "finish_candidate_build",
        "read_candidate_build",
        "internal_link_sources",
        "seal_internal_link_revision",
    }
)


class WeeklySkillConnection:
    """The opaque workload handle occupies no identity-session table or role."""

    def __init__(self, connection, permit: SkillPermit, *, binding_id: UUID | None = None):
        self.connection = connection
        self.permit = permit
        self.binding_id = binding_id
        self.autocommit = connection.autocommit
        self.info = connection.info

    def transaction(self):
        return self.connection.transaction()

    def execute(self, query, params=None):
        match = _CALL.fullmatch(query)
        if match is not None:
            name = match[2]
            if name in _LINK_PORTS:
                scope = (
                    (self.permit.handle_hash, self.permit.generation, self.permit.site_id)
                    if name in {"internal_link_sources", "seal_internal_link_revision"}
                    else (self.permit.handle_hash, self.permit.site_id, self.permit.generation)
                )
                if (
                    self.permit.stage != "internal_link_proposals"
                    or params is None
                    or params[:3] != scope
                ):
                    raise PermissionError("Internal-link workload scope unavailable.")
                return self.connection.execute(
                    f"{match[1]}internal_link_skill_{name}{match[3]}", params
                )
            if name in _OWNER_SHAPED:
                if params is None or params[:3] != (
                    self.permit.handle_hash,
                    self.permit.generation,
                    self.permit.site_id,
                ):
                    raise PermissionError("Skill handle scope mismatch.")
                if name in {"business_brain_begin_extraction", "seo_strategy_record"}:
                    from hashlib import md5

                    label = (
                        str(params[5])
                        if name == "business_brain_begin_extraction"
                        else "strategy_rebuild"
                    )
                    digest = md5((str(self.permit.cycle_id) + label).encode()).hexdigest()
                    identifier = UUID(
                        f"{digest[:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:]}"
                    )
                    params = (*params[:3], identifier, *params[4:])
            elif name in _IMPORT:
                if params is None or params[:2] != (self.permit.tenant_id, self.permit.site_id):
                    raise PermissionError("Skill import scope mismatch.")
                params = (self.permit.handle_hash, self.permit.generation, *params)
            else:
                raise PermissionError("Skill database port unavailable.")
            query = f"{match[1]}weekly_skill_{name}{match[3]}"
            if name in _IMPORT:
                query = query.replace("(", "(%s,%s,", 1)
            if name in {"ga4_owner_binding", "current_gsc_binding", "current_bing_binding"}:
                if self.binding_id is None:
                    raise PermissionError("An admitted import binding is required.")
                query += " WHERE binding_id=%s"
                params = (*params, self.binding_id)
            return self.connection.execute(query, params)
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
        if query in {"SELECT pg_try_advisory_lock(%s)", "SELECT pg_advisory_unlock(%s)"}:
            if self.binding_id is None:
                raise PermissionError("Import lock scope unavailable.")
            from hashlib import sha256

            key = int.from_bytes(sha256(self.binding_id.bytes).digest()[:8], "big", signed=True)
            if params != (key,):
                raise PermissionError("Import lock scope mismatch.")
            return self.connection.execute(query, params)
        raise PermissionError("Skill database statement unavailable.")


class GuardedSecrets:
    def __init__(self, source, guard):
        self.source, self.guard = source, guard

    async def refresh_token(self, *args, **kwargs):
        self.guard()
        return await self.source.refresh_token(*args, **kwargs)

    async def client_credentials(self, *args, **kwargs):
        self.guard()
        return await self.source.client_credentials(*args, **kwargs)

    async def replace_refresh_token(self, *args, **kwargs):
        self.guard()
        return await self.source.replace_refresh_token(*args, **kwargs)

    async def api_key(self, *args, **kwargs):
        self.guard()
        return await self.source.api_key(*args, **kwargs)


class GuardedFetcher:
    def __init__(self, fetcher, guard):
        self.fetcher, self.guard = fetcher, guard

    def request(self, *args, **kwargs):
        self.guard()
        return self.fetcher.request(*args, **kwargs)


class GuardedGateway(SharedEgressProvider):
    def __init__(self, gateway, permit, guard, profiles):
        gateways = (
            dict(gateway)
            if isinstance(gateway, Mapping)
            else {profile: gateway for profile in profiles}
        )
        if set(gateways) != profiles:
            raise PermissionError("Closed skill egress profiles are required.")
        self.gateways = {}
        for profile, provider in gateways.items():
            if not isinstance(provider, SharedEgressProvider) or isinstance(
                provider.run, OwnerConnectorContext
            ):
                raise PermissionError("A real workload shared-egress context is required.")
            if (provider.run.tenant_id, provider.run.site_id) != (permit.tenant_id, permit.site_id):
                raise PermissionError("Skill egress scope mismatch.")
            self.gateways[profile] = replace(
                provider, fetcher=GuardedFetcher(provider.fetcher, guard)
            )
        self.guard, self.profiles = guard, profiles
        self.last_request = {}
        purposes = {provider.purpose for provider in gateways.values()}
        if len(purposes) != 1:
            raise PermissionError("Skill egress purpose mismatch.")
        object.__setattr__(self, "purpose", purposes.pop())
        # Every leaf above is bound to this permit's tenant/site; expose that
        # existing scope for the monthly model-budget check, not a new transport.
        object.__setattr__(self, "run", next(iter(self.gateways.values())).run)

    def request_json(self, **kwargs):
        self.guard()
        if kwargs.get("profile") not in self.profiles:
            raise PermissionError("Skill egress profile unavailable.")
        return self._send("request_json", kwargs)

    def post_json(self, **kwargs):
        self.guard()
        if kwargs.get("profile") not in self.profiles:
            raise PermissionError("Skill egress profile unavailable.")
        return self._send("post_json", kwargs)

    def _send(self, method, kwargs):
        provider = self.gateways[kwargs["profile"]]
        origin = PROFILE_RULES[kwargs["profile"]].origin
        wait = (
            self.last_request.get(origin, 0)
            + provider.admission_policy.min_delay_ms / 1000
            + 0.05
            - time.monotonic()
        )
        if wait > 0:
            time.sleep(wait)
        self.guard()
        try:
            return getattr(provider, method)(**self._bounded(kwargs))
        finally:
            self.last_request[origin] = time.monotonic()

    def _bounded(self, kwargs):
        # Older owner adapters request larger envelopes. Workload ports only
        # reduce those bounds to the shared profile and admitted run policy.
        provider = self.gateways[kwargs["profile"]]
        rules = PROFILE_RULES[kwargs["profile"]]
        return {
            **kwargs,
            "max_response_bytes": min(
                kwargs["max_response_bytes"],
                rules.max_response_bytes,
                provider.policy.max_body_bytes,
            ),
            "timeout_seconds": min(
                kwargs["timeout_seconds"],
                rules.max_timeout_seconds,
                provider.policy.request_timeout_seconds,
            ),
        }


@dataclass(frozen=True)
class ImportSkillPort:
    connection_factory: object
    gateway: SharedEgressProvider | Mapping[EgressProfile, SharedEgressProvider] | None
    secrets_store: object | None
    provider: str

    def __post_init__(self):
        if self.provider not in {"gsc", "bing", "ga4"}:
            raise ValueError("Unknown weekly import provider.")

    @property
    def configured(self):
        return self.gateway is not None and self.secrets_store is not None

    async def run(self, permit, guard):
        profiles = {
            "gsc": {EgressProfile.GOOGLE_OAUTH_TOKEN, EgressProfile.GSC_API},
            "bing": {EgressProfile.BING_OAUTH_TOKEN, EgressProfile.BING_API},
            "ga4": {EgressProfile.GOOGLE_OAUTH_TOKEN, EgressProfile.GA4_DATA},
        }[self.provider]
        gateway = GuardedGateway(self.gateway, permit, guard, profiles)
        secrets = GuardedSecrets(self.secrets_store, guard)
        end = date.fromisoformat(permit.week_start) - timedelta(days=3)
        start = end - timedelta(days=27)
        refs = []
        for unit in permit.plan:
            guard()
            with self.connection_factory() as raw:
                connection = WeeklySkillConnection(raw, permit, binding_id=UUID(unit["binding_id"]))
                if self.provider == "gsc":
                    imported = await import_gsc_analytics(
                        connection,
                        secrets,
                        gateway,
                        tenant_id=permit.tenant_id,
                        site_id=permit.site_id,
                        start_date=start,
                        end_date=end,
                        dimensions=("query", "page"),
                        refresh_operation_id=uuid4(),
                        query_operation_id=uuid4(),
                        data_state="final",
                    )
                    identifier = imported.generation_id
                elif self.provider == "bing":
                    for kind in ("performance", "page_performance"):
                        guard()
                        imported = await import_bing_observation(
                            connection,
                            secrets,
                            gateway,
                            tenant_id=permit.tenant_id,
                            site_id=permit.site_id,
                            kind=kind,
                            refresh_operation_id=uuid4(),
                            import_operation_id=uuid4(),
                        )
                        refs.append(f"bing_import:{imported.generation_id}")
                    continue
                else:
                    service = Ga4Service(connection, secrets, permit.generation, "")
                    imported = await service.import_report(
                        permit.handle,
                        permit.site_id,
                        start_date=start,
                        end_date=end,
                        egress=gateway,
                    )
                    identifier = imported["generation_id"]
                refs.append(f"{self.provider}_import:{identifier}")
        return SkillResult("completed", "IMPORT_RECORDED", tuple(refs))


@dataclass(frozen=True)
class StrategySkillPort:
    connection_factory: object
    configured: bool = True
    research: object | None = None

    async def run(self, permit, guard):
        guard()
        with self.connection_factory() as raw:
            result = await rebuild_strategy(
                WeeklySkillConnection(raw, permit),
                session_token=permit.handle,
                generation=permit.generation,
                site_id=permit.site_id,
                research=self.research,
                guard=guard,
            )
        return SkillResult("completed", "STRATEGY_RECORDED", (f"strategy:{result['snapshot_id']}",))


@dataclass(frozen=True)
class BriefSkillPort:
    connection_factory: object
    configured: bool = True

    async def run(self, permit, guard):
        refs = []
        for unit in permit.plan:
            guard()
            validate_brief(unit["payload"])
            with self.connection_factory() as raw:
                connection = WeeklySkillConnection(raw, permit)
                with _clean_transaction(connection):
                    outcome = connection.execute(
                        "SELECT control.content_writer_create_brief(%s,%s,%s,%s,%s,%s,%s)",
                        (
                            permit.handle_hash,
                            permit.generation,
                            permit.site_id,
                            UUID(unit["brief_id"]),
                            Jsonb(unit["payload"]),
                            "evidence_proposal",
                            None,
                        ),
                    ).fetchone()[0]
            if outcome != "created":
                return SkillResult("unavailable", "BRIEF_EVIDENCE_CHANGED", tuple(refs))
            refs.append(f"brief:{unit['brief_id']}")
        return SkillResult("completed", "BRIEFS_PROPOSED", tuple(refs))


@dataclass(frozen=True)
class ReportSkillPort:
    connection_factory: object
    configured: bool = True

    async def run(self, permit, guard):
        guard()
        with self.connection_factory() as raw, _clean_transaction(raw):
            result = raw.execute(
                "SELECT control.weekly_skill_queue_report(%s,%s,%s)",
                (permit.handle_hash, permit.generation, permit.site_id),
            ).fetchone()[0]
        if result["state"] == "completed":
            return SkillResult("completed", "REPORT_QUEUED", tuple(result["evidence_refs"]))
        return SkillResult("unavailable", result["reason"])


@dataclass(frozen=True)
class ChatReportSkillPort:
    connection_factory: object
    delivery: object | None

    @property
    def configured(self):
        return self.delivery is not None and self.delivery.configured

    async def run(self, permit, guard):
        if (self.delivery.scope.tenant_id, self.delivery.scope.site_id) != (
            permit.tenant_id,
            permit.site_id,
        ):
            raise PermissionError("Chat delivery scope mismatch.")
        guard()
        with self.connection_factory() as raw, _clean_transaction(raw):
            result = raw.execute(
                "SELECT control.weekly_skill_queue_chat_report(%s,%s,%s)",
                (permit.handle_hash, permit.generation, permit.site_id),
            ).fetchone()[0]
        refs = tuple(result["evidence_refs"])
        if result["state"] != "queued":
            return SkillResult("unavailable", result["reason"])
        states = await self.delivery.deliver([UUID(ref.split(":", 1)[1]) for ref in refs])
        if all(state == "accepted" for state in states):
            return SkillResult("completed", "CHAT_REPORT_ACCEPTED", refs)
        if any(state == "unknown" for state in states):
            return SkillResult("failed", "CHAT_REPORT_OUTCOME_UNKNOWN", refs)
        return SkillResult("unavailable", "CHAT_REPORT_NOT_ACCEPTED", refs)


class GuardedArtifactStore:
    def __init__(self, store, guard):
        self.store, self.guard = store, guard

    def read(self, *args, **kwargs):
        self.guard()
        return self.store.read(*args, **kwargs)


@dataclass(frozen=True)
class PageSpeedSkillPort:
    connection_factory: object
    gateway: SharedEgressProvider | None
    credentials: object | None

    @property
    def configured(self):
        return self.gateway is not None and self.credentials is not None

    async def run(self, permit, guard):
        from dataclasses import replace

        guard()
        if (self.gateway.run.tenant_id, self.gateway.run.site_id) != (
            permit.tenant_id,
            permit.site_id,
        ):
            raise PermissionError("PageSpeed egress scope mismatch.")
        with self.connection_factory() as admission, self.connection_factory() as ingest:
            gateway = replace(
                self.gateway,
                admission_connection=admission,
                ingest_connection=ingest,
                run=WeeklyPageSpeedContext(
                    permit.tenant_id, permit.site_id, permit.handle, permit.generation
                ),
                fetcher=GuardedFetcher(self.gateway.fetcher, guard),
            )
            try:
                result = await collect_pagespeed_plan(
                    WeeklySkillConnection(ingest, permit),
                    egress=gateway,
                    credentials=GuardedSecrets(self.credentials, guard),
                    plan={"week_start": permit.week_start, "samples": list(permit.plan)},
                    authority_token=permit.handle,
                    generation=permit.generation,
                    site_id=permit.site_id,
                )
            except PageSpeedCredentialUnavailable:
                return SkillResult("unavailable", "PSI_CREDENTIAL_UNAVAILABLE")
        outcomes = result["outcomes"]
        refs = tuple(f"pagespeed:{item['sample_id']}" for item in outcomes)
        if outcomes and all(item["state"] == "observed" for item in outcomes):
            return SkillResult(
                "completed",
                "PSI_OBSERVED_KEYLESS"
                if result["credential_mode"] == "keyless_quota"
                else "PSI_OBSERVED",
                refs,
            )
        return SkillResult(
            "unavailable",
            next(
                (item["reason"] for item in outcomes if item.get("reason")),
                "PSI_OBSERVATION_INCOMPLETE",
            ),
            refs,
        )


@dataclass(frozen=True)
class VisibilitySkillPort:
    activities: object | None

    @property
    def configured(self):
        return (
            self.activities is not None
            and self.activities.credentials is not None
            and self.activities.egress_factory is not None
            and bool(self.activities.ceilings)
        )

    @property
    def cost_bound_cents(self):
        return (
            (max(self.activities.ceilings.values(), default=0) + 9999) // 10000
            if self.activities
            else 0
        )

    async def run(self, permit, guard):
        from hashlib import md5

        from signal_core.ai_visibility_schedule import (
            VisibilityActivities,
            VisibilityRun,
            VisibilitySite,
        )

        source = self.activities

        class GrantedActivities(VisibilityActivities):
            def _query(self, function, args):
                if function not in {
                    "open_ai_visibility_run",
                    "reserve_ai_visibility_call",
                    "ai_visibility_call_permit",
                    "finish_ai_visibility_call",
                } or args[:2] != (permit.tenant_id, permit.site_id):
                    raise PermissionError("Visibility port scope rejected.")
                if function != "finish_ai_visibility_call":
                    guard()
                with self.connection_factory() as raw, _clean_transaction(raw):
                    return raw.execute(
                        "SELECT * FROM control.weekly_skill_"
                        + function
                        + "("
                        + ",".join(["%s"] * (len(args) + 2))
                        + ")",
                        (permit.handle_hash, permit.generation, *args),
                    ).fetchone()

        digest = md5((str(permit.cycle_id) + "visibility_reobserve").encode()).hexdigest()
        run_id = UUID(
            f"{digest[:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:]}"
        )

        async def egress(site, operation):
            guard()
            gateway = await source.egress_factory(site, operation)
            if isinstance(gateway, Mapping):
                profiles = {
                    "openai": EgressProfile.OPENAI_ASSISTANT,
                    "perplexity": EgressProfile.PERPLEXITY_ASSISTANT,
                    "gemini": EgressProfile.GEMINI_ASSISTANT,
                }
                return GuardedGateway(
                    gateway, permit, guard, {profiles[name] for name in source.ceilings}
                )
            if not isinstance(gateway, SharedEgressProvider):
                raise PermissionError("A real assistant shared-egress gateway is required.")
            if (gateway.run.tenant_id, gateway.run.site_id) != (permit.tenant_id, permit.site_id):
                raise PermissionError("Assistant egress scope mismatch.")
            return replace(gateway, fetcher=GuardedFetcher(gateway.fetcher, guard))

        activities = GrantedActivities(
            source.connection_factory,
            source.evidence_connection_factory,
            source.recovery_source,
            credentials=GuardedSecrets(source.credentials, guard),
            egress_factory=egress,
            ceilings=source.ceilings,
        )
        result = await activities.run(
            VisibilityRun(VisibilitySite(str(permit.tenant_id), str(permit.site_id)), str(run_id))
        )
        if result["state"] == "complete":
            return SkillResult("completed", "VISIBILITY_OBSERVED", (f"visibility_run:{run_id}",))
        return SkillResult(
            "unavailable",
            result.get("reason", "VISIBILITY_OBSERVATION_INCOMPLETE"),
            (f"visibility_run:{run_id}",),
        )


@dataclass(frozen=True)
class BrainSkillPort:
    connection_factory: object
    extractor: BusinessBrainExtractor | None

    @property
    def configured(self):
        return self.extractor is not None and self.extractor.configured

    async def run(self, permit, guard):
        model = self.extractor.model
        primary = self.extractor.primary
        if primary.credential is not None and primary.egress is not None:
            primary = replace(
                primary,
                credential=GuardedSecrets(primary.credential, guard),
                egress=GuardedGateway(primary.egress, permit, guard, {EgressProfile.JEV}),
            )
        extractor = replace(
            self.extractor,
            store=GuardedArtifactStore(self.extractor.store, guard),
            model=replace(
                model,
                credential=GuardedSecrets(model.credential, guard),
                egress=GuardedGateway(model.egress, permit, guard, {EgressProfile.OPENAI_MODEL}),
            ),
            primary=primary,
        )
        refs = []
        for unit in permit.plan:
            guard()
            source = UUID(unit["source_id"])
            with self.connection_factory() as raw:
                connection = WeeklySkillConnection(raw, permit)
                selected_range = None
                if unit["source_kind"] == "brand_document":
                    document = read_brand_document(
                        connection,
                        extractor.store,
                        extractor.key,
                        session_token=permit.handle,
                        current_recovery_generation=permit.generation,
                        site_id=permit.site_id,
                        document_id=source,
                    )
                    selected_range = {"start": 0, "end": min(len(document.text), 24000)}
                result = await extractor.extract(
                    connection,
                    session_token=permit.handle,
                    current_recovery_generation=permit.generation,
                    site_id=permit.site_id,
                    provenance=FactProvenance(unit["source_kind"], source, selected_range),
                )
            if result.extraction_id is not None:
                refs.append(f"brain_extraction:{result.extraction_id}")
            if result.state != "completed":
                return SkillResult("failed", "EXTRACTION_INCOMPLETE", tuple(refs))
        return SkillResult("completed", "FACTS_PROPOSED", tuple(refs))


def local_skill_ports(connection_factory, *, strategy_research=None):
    return {
        "strategy_rebuild": StrategySkillPort(connection_factory, research=strategy_research),
        "brief_proposals": BriefSkillPort(connection_factory),
        "report_delivery": ReportSkillPort(connection_factory),
    }
