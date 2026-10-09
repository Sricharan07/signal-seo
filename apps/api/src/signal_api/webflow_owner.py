"""Optional owner composition. It never exposes the lab-only delivery port."""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field

from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import CONNECTOR_FACTORY_RULES
from signal_core.owner_connector_egress import OwnerConnectorContext
from signal_core.session_tokens import hash_session_token
from signal_core.webflow import WebflowUnavailable
from signal_core.webflow_service import WebflowService

from signal_api.integration_connectors import OwnerOnboardingProvider


@dataclass(frozen=True, repr=False)
class WebflowOwnerEgressFactory:
    connection_factory: object = field(repr=False)
    store: object = field(repr=False)
    artifact_key: object = field(repr=False)
    fetcher: object = field(repr=False)

    @contextmanager
    def __call__(self, token, site, generation, origin):
        if origin not in CONNECTOR_FACTORY_RULES["webflow"].origins:
            raise WebflowUnavailable("WEBFLOW_EGRESS_UNAVAILABLE")
        with self.connection_factory() as admission, self.connection_factory() as ingest:
            with _clean_transaction(admission):
                row = admission.execute(
                    "SELECT tenant_id,role_key,authentication_level,outcome "
                    "FROM control.resolve_snapshot_authority(%s,%s,%s)",
                    (hash_session_token(token), site, generation),
                ).fetchone()
            if row is None or row[1:] != ("owner", "mfa", "authorized"):
                raise WebflowUnavailable("WEBFLOW_OWNER_ACCESS_OR_SOURCE_UNAVAILABLE")
            yield OwnerOnboardingProvider(
                admission,
                ingest,
                self.store,
                OwnerConnectorContext(row[0], site, token, generation),
                CONNECTOR_FACTORY_RULES["webflow"].policy((origin,)),
                self.fetcher,
                "owner-webflow",
                OriginAdmissionPolicy(),
                self.artifact_key,
                purpose="connector",
            )


@dataclass(frozen=True, repr=False)
class ComposedWebflowGateway:
    connection_factory: object = field(repr=False)
    recovery_authority: object = field(repr=False)
    egress_factory: object = field(repr=False)
    secrets_store: object = field(repr=False)
    dashboard_origin: str
    secret_options: dict = field(default_factory=dict, repr=False)

    async def service(self, connection):
        generation = await self.recovery_authority.current_generation()
        return WebflowService(
            connection,
            self.secrets_store,
            self.dashboard_origin,
            generation.value,
            openbao_options=self.secret_options,
        )

    async def read_webflow(self, *, session_token, site_id):
        with self.connection_factory() as connection:
            service = await self.service(connection)
            return service.read(session_token=session_token, site_id=site_id)

    async def webflow_options(self, *, session_token, site_id):
        with self.connection_factory() as connection:
            service = await self.service(connection)
            return service.call(session_token, site_id, "options")

    async def review_webflow(self, *, session_token, site_id, values):
        with self.connection_factory() as connection:
            service = await self.service(connection)
            result = service.review(
                session_token=session_token,
                site_id=site_id,
                revision_id=values["id"],
                revision_sha256=values["revision_sha256"],
                decision=values["decision"],
            )
            if result.get("state") not in {"reviewed", "replayed"}:
                raise WebflowUnavailable("WEBFLOW_REVIEW_CONFLICT")
            return result

    async def mutate_webflow(self, *, session_token, site_id, operation, values):
        with self.connection_factory() as connection:
            service = await self.service(connection)
            common = dict(session_token=session_token, site_id=site_id)
            if operation == "begin":
                return await service.begin(**common, **values)
            if operation == "seal":
                return service.seal(**common, **values)
            if operation not in {"complete", "revoke"}:
                raise WebflowUnavailable("WEBFLOW_ACTION_UNAVAILABLE")
            if operation == "revoke":
                # A missing upstream gateway must never prevent the local restriction.
                service.call(session_token, site_id, "revoke", {"id": str(values["binding_id"])})
                with ExitStack() as stack:
                    try:
                        egress = stack.enter_context(
                            self.egress_factory(
                                session_token,
                                site_id,
                                service.recovery_generation,
                                "https://webflow.com",
                            )
                        )
                    except Exception:
                        egress = None
                    return await service.revoke(**common, **values, egress=egress)
            # Authority is checked before allocating any credential-bearing egress.
            service.call(session_token, site_id, "preflight")
            origin = "https://api.webflow.com"
            with self.egress_factory(
                session_token, site_id, service.recovery_generation, origin
            ) as egress:
                return await getattr(service, operation)(**common, **values, egress=egress)
