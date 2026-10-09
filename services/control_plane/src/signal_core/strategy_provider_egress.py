"""Narrow, deployment-injected shared egress for owner and weekly paid research."""

import time
from contextlib import contextmanager
from dataclasses import dataclass

from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_http import PinnedHttpFetcher
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import CONNECTOR_FACTORY_RULES
from signal_core.owner_connector_egress import WeeklyDataForSeoContext
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider


class StrategyResearchProvider(SharedEgressProvider):
    def request_json(self, **kwargs):
        # Only a pre-dispatch deferral is retryable, with the same reserved intent.
        deadline = time.monotonic() + 5
        while True:
            try:
                return super().request_json(**kwargs)
            except ProviderEgressUnavailable as error:
                if error.code != "EGRESS_DEFERRED" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)


@dataclass(frozen=True, repr=False)
class StrategyProviderEgressFactory:
    connection_factory: object
    store: object
    artifact_key: object
    resolver: object

    @contextmanager
    def __call__(self, token, site_id, generation):
        with self.connection_factory() as admission, self.connection_factory() as ingest:
            with _clean_transaction(admission):
                current = admission.execute(
                    "SELECT tenant_id FROM control.strategy_provider_egress_context(%s,%s,%s)",
                    (hash_session_token(token), generation, site_id),
                ).fetchone()
            if current is None:
                raise ProviderEgressUnavailable("EGRESS_AUTHORITY_UNAVAILABLE", retryable=False)
            yield StrategyResearchProvider(
                admission,
                ingest,
                self.store,
                WeeklyDataForSeoContext(current[0], site_id, token, generation),
                CONNECTOR_FACTORY_RULES["strategy_dataforseo"].policy(),
                PinnedHttpFetcher(self.resolver),
                "strategy-provider-research",
                OriginAdmissionPolicy(),
                self.artifact_key,
                purpose="connector",
            )
