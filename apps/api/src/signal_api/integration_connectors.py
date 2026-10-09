"""Exact dedicated-test connector composition; no general provider authority."""

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_http import CrawlFetchRejected, PinnedHttpFetcher
from signal_core.crawl_urls import validate_public_addresses
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import CONNECTOR_FACTORY_RULES
from signal_core.integration_scope import IntegrationScope, load_integration_scope
from signal_core.owner_connector_egress import OwnerConnectorContext
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.slack_protocol import SlackRejected

from signal_api.slack_http import ComposedSlackGateway, SlackInstall

HOSTS = frozenset(
    urlsplit(origin).hostname
    for name in ("slack", "github", "gsc")
    for origin in CONNECTOR_FACTORY_RULES[name].origins
)
ROLES = ("slack-connector", "gsc-connector", "github-reader", "owner-artifact-reader")
ORIGINS = {name: CONNECTOR_FACTORY_RULES[name].origins for name in ("slack", "github", "gsc")}


class IntegrationProviderResolver:
    def __init__(self, path, private_reader, *, clock=time.time):
        self.path, self.private_reader, self.clock = path, private_reader, clock

    def __call__(self, host, port, timeout):
        try:
            document = json.loads(self.private_reader(self.path))
            if (
                host not in HOSTS
                or port != 443
                or set(document) != {"hosts", "issued_at", "expires_at"}
                or not isinstance(document["hosts"], dict)
                or set(document["hosts"]) != HOSTS
                or type(document["issued_at"]) is not int
                or type(document["expires_at"]) is not int
                or not document["issued_at"] <= self.clock() < document["expires_at"]
                or not 0 < document["expires_at"] - document["issued_at"] <= 3600
                or any(not isinstance(address, str) for address in document["hosts"].values())
            ):
                raise ValueError
            validate_public_addresses(tuple(document["hosts"].values()))
            return validate_public_addresses((document["hosts"][host],))
        except Exception:
            raise CrawlFetchRejected("Dedicated provider pins are unavailable.") from None


class OwnerOnboardingProvider(SharedEgressProvider):
    def request_json(self, **kwargs):
        # Admission deferral precedes dispatch. Keep the exact operation identity;
        # never retry an unknown dispatch or synthesize a terminal response.
        deadline = time.monotonic() + 5
        while True:
            try:
                return super().request_json(**kwargs)
            except ProviderEgressUnavailable as error:
                if error.code != "EGRESS_DEFERRED" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)


@dataclass(frozen=True, repr=False)
class OwnerEgressFactory:
    connection_factory: object
    store: EncryptedLocalArtifactStore
    key: ArtifactEncryptionKey
    resolver: IntegrationProviderResolver
    connector: str
    integration_scope: IntegrationScope = field(
        default_factory=load_integration_scope, kw_only=True
    )

    def __post_init__(self):
        if self.connector not in ORIGINS:
            raise ValueError("Unknown dedicated connector.")

    @contextmanager
    def __call__(self, session_token, site_id, generation):
        if site_id != self.integration_scope.site:
            raise ValueError("The dedicated connector site is required.")
        with self.connection_factory() as admission, self.connection_factory() as ingest:
            with _clean_transaction(admission):
                row = admission.execute(
                    "SELECT tenant_id,role_key,authentication_level,outcome "
                    "FROM control.resolve_snapshot_authority(%s,%s,%s)",
                    (hash_session_token(session_token), site_id, generation),
                ).fetchone()
            if row is None or row[1:] != ("owner", "mfa", "authorized"):
                raise ValueError("Current dedicated Owner/MFA authority is required.")
            yield OwnerOnboardingProvider(
                admission,
                ingest,
                self.store,
                OwnerConnectorContext(row[0], site_id, session_token, generation),
                CONNECTOR_FACTORY_RULES[self.connector].policy(),
                PinnedHttpFetcher(self.resolver),
                "owner-connector-onboarding",
                OriginAdmissionPolicy(),
                self.key,
                purpose="connector",
            )


@dataclass(frozen=True, repr=False)
class IntegrationSlackGateway(ComposedSlackGateway):
    integration_scope: IntegrationScope = field(
        default_factory=load_integration_scope, kw_only=True
    )

    async def read(self, session_token, site_id):
        if site_id != self.integration_scope.site:
            raise SlackRejected("SLACK_AUTHORITY_DENIED")
        return await super().read(session_token, site_id)

    async def control(self, session_token, site_id, command):
        if site_id != self.integration_scope.site or (
            isinstance(command, SlackInstall)
            and (
                command.workspace_id != self.integration_scope.slack_workspace_id
                or command.channel_id != self.integration_scope.slack_channel_id
            )
        ):
            raise SlackRejected("SLACK_AUTHORITY_DENIED")
        return await super().control(session_token, site_id, command)


async def read_owner_artifact_key(base_url, token, context):
    from signal_core.openbao_http import json_document, request

    response = await request(
        base_url=base_url,
        token=token,
        method="GET",
        path="/signal-identity/data/platform/owner-artifacts",
        verify=context,
    )
    if response.status_code != 200:
        raise RuntimeError("Dedicated artifact key unavailable.")
    envelope = json_document(response)["data"]
    data, metadata = envelope["data"], envelope["metadata"]
    if (
        set(data) != {"reference", "material_hex"}
        or type(metadata["version"]) is not int
        or metadata["version"] != 1
        or metadata["destroyed"] is not False
        or metadata["deletion_time"] not in {None, ""}
        or data["reference"] != "owner-robots:test-v1"
        or not isinstance(data["material_hex"], str)
        or len(data["material_hex"]) != 64
    ):
        raise RuntimeError("Dedicated artifact key rejected.")
    return ArtifactEncryptionKey(data["reference"], bytes.fromhex(data["material_hex"]))


def admitted_connector_route(method, path, integration_scope):
    return method in {"GET", "POST"} and path in {
        f"/v1/sites/{integration_scope.site}/{connector}"
        for connector in ("slack", "gsc", "github", "github-pr")
    }


def protected_artifact_store():
    return EncryptedLocalArtifactStore(
        Path("/var/lib/signal-owner-robots"), max_plaintext_bytes=256 * 1024
    )
