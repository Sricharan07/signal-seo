"""Explicitly configured live qualification; never enables writes by default."""

import argparse
import asyncio
import json
import os
import sys
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from temporalio.client import Client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.ai_visibility_schedule import VisibilityActivities  # noqa: E402
from signal_core.assistant_credentials import OpenBaoAssistantCredentials  # noqa: E402
from signal_core.autonomy_gate import AutonomyGate  # noqa: E402
from signal_core.bing_secrets import OpenBaoBingSecrets  # noqa: E402
from signal_core.business_brain_extraction import BusinessBrainExtractor  # noqa: E402
from signal_core.candidate_sandbox import DockerCandidateSandbox  # noqa: E402
from signal_core.chat_reports import ChatDelivery  # noqa: E402
from signal_core.crawl_admission import OriginAdmissionPolicy  # noqa: E402
from signal_core.crawl_artifacts import (  # noqa: E402
    ArtifactEncryptionKey,
    EncryptedLocalArtifactStore,
)
from signal_core.crawl_frontier import CrawlRunOpened  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver, PinnedHttpFetcher  # noqa: E402
from signal_core.crawl_urls import CrawlScopePolicy  # noqa: E402
from signal_core.database import Scope  # noqa: E402
from signal_core.decision_records import PostgresDecisionRecorder  # noqa: E402
from signal_core.egress_profiles import EgressProfile  # noqa: E402
from signal_core.ga4_secrets import OpenBaoGa4Secrets  # noqa: E402
from signal_core.github_read_binding import (  # noqa: E402
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
)
from signal_core.gsc_secrets import OpenBaoGscSecrets  # noqa: E402
from signal_core.jev_decisions import DecisionService, JevHttpAdapter  # noqa: E402
from signal_core.live_verification import SharedLiveVerifier  # noqa: E402
from signal_core.model_credentials import OpenBaoJevCredential, OpenBaoModelCredential  # noqa: E402
from signal_core.model_reasoning import BusinessBrainModelAdapter  # noqa: E402
from signal_core.pagespeed_credentials import OpenBaoPageSpeedCredentials  # noqa: E402
from signal_core.recovery_authority import OpenBaoRecoveryAuthority  # noqa: E402
from signal_core.shared_egress import SharedEgressProvider  # noqa: E402
from signal_core.slack_secrets import OpenBaoSlackSecrets  # noqa: E402
from signal_core.telegram_secrets import OpenBaoTelegramSecrets  # noqa: E402
from signal_core.weekly_delivery import (  # noqa: E402
    DeliveryWeeklyWork,
    WeeklyDeliveryIO,
    WeeklyTechnicalDelivery,
)
from signal_core.weekly_delivery_runtime import WeeklyDeliveryRuntime  # noqa: E402
from signal_core.weekly_loop import WeeklyActivities, WeeklyCycleStore, WeeklySite  # noqa: E402
from signal_core.weekly_observation import CrawlWeeklyWork  # noqa: E402
from signal_core.weekly_schedule import WeeklyScheduleReconciler  # noqa: E402
from signal_core.weekly_skill_ports import (  # noqa: E402
    BrainSkillPort,
    ChatReportSkillPort,
    ImportSkillPort,
    PageSpeedSkillPort,
    VisibilitySkillPort,
    local_skill_ports,
)
from signal_core.weekly_skills import WeeklySkills  # noqa: E402
from signal_core.write_intent_journal import WriteIntentJournal  # noqa: E402

REQUIRED = (
    "SIGNAL_WEEKLY_WORKFLOW_DSN",
    "SIGNAL_WEEKLY_API_DSN",
    "SIGNAL_WEEKLY_INGEST_DSN",
    "SIGNAL_WEEKLY_ADMISSION_DSN",
    "SIGNAL_WEEKLY_JOURNAL_DSN",
    "SIGNAL_WEEKLY_JOURNAL_KEY",
    "SIGNAL_WEEKLY_JOURNAL_ENCRYPTION_KEY",
    "SIGNAL_WEEKLY_OPENBAO_URL",
    "SIGNAL_WEEKLY_RECOVERY_TOKEN",
    "SIGNAL_WEEKLY_GITHUB_TOKEN",
    "SIGNAL_WEEKLY_JEV_TOKEN",
    "SIGNAL_WEEKLY_GITHUB_CONTEXT",
    "SIGNAL_WEEKLY_JEV_CONTEXT",
    "SIGNAL_WEEKLY_LIVE_CONTEXT",
    "SIGNAL_WEEKLY_TEMPORAL_ADDRESS",
    "SIGNAL_WEEKLY_TEMPORAL_NAMESPACE",
)


def secret_file(path: str, maximum: int = 16384) -> bytes:
    source = Path(path)
    if not source.is_absolute() or not source.is_file():
        raise ValueError("JOURNAL_KEY_FILE_INVALID")
    with source.open("rb") as stream:
        value = stream.read(maximum + 1)
    if not value or len(value) > maximum:
        raise ValueError("JOURNAL_KEY_FILE_INVALID")
    return value


def context(path: str, scope: Scope, origin: str, maximum: int, timeout: int):
    source = Path(path)
    if not source.is_absolute() or source.stat().st_size > 65536:
        raise ValueError("EGRESS_CONTEXT_INVALID")
    document = json.loads(source.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "run", "policy", "artifact_root"}
        or document["schema_version"] != 1
    ):
        raise ValueError("EGRESS_CONTEXT_INVALID")
    raw = document["run"]
    if set(raw) != {
        "tenant_id",
        "site_id",
        "command_id",
        "run_id",
        "root_frontier_id",
        "root_url_id",
        "first_run_id",
        "started_at",
        "status",
    }:
        raise ValueError("EGRESS_CONTEXT_INVALID")
    run = CrawlRunOpened(
        **{
            **raw,
            **{
                k: UUID(raw[k])
                for k in (
                    "tenant_id",
                    "site_id",
                    "command_id",
                    "run_id",
                    "root_frontier_id",
                    "root_url_id",
                )
            },
            "started_at": datetime.fromisoformat(raw["started_at"]),
            "duplicate": False,
        }
    )
    raw_policy = document["policy"]
    if set(raw_policy) != {
        "schema_version",
        "allowed_origins",
        "user_agent",
        "max_redirects",
        "max_body_bytes",
        "request_timeout_seconds",
        "total_timeout_seconds",
    }:
        raise ValueError("EGRESS_CONTEXT_INVALID")
    policy = CrawlScopePolicy(
        **{**raw_policy, "allowed_origins": tuple(raw_policy["allowed_origins"])}
    )
    root = Path(document["artifact_root"])
    if (
        run.tenant_id != scope.tenant_id
        or run.site_id != scope.site_id
        or run.status != "running"
        or run.started_at.tzinfo is None
        or policy.allowed_origins != (origin,)
        or policy.max_redirects != 0
        or policy.max_body_bytes > maximum
        or policy.request_timeout_seconds > timeout
        or not root.is_absolute()
    ):
        raise ValueError("EGRESS_CONTEXT_SCOPE_REJECTED")
    return run, policy, EncryptedLocalArtifactStore(root)


async def qualify(args):
    scope = Scope(args.tenant_id, args.site_id)
    site = WeeklySite(str(args.tenant_id), str(args.site_id), str(args.grant_id))

    def workflow_connection():
        return psycopg.connect(
            os.environ["SIGNAL_WEEKLY_WORKFLOW_DSN"], autocommit=True, connect_timeout=5
        )

    def release_connection():
        return psycopg.connect(
            os.environ["SIGNAL_WEEKLY_API_DSN"], autocommit=True, connect_timeout=5
        )

    def ingest_connection():
        return psycopg.connect(
            os.environ["SIGNAL_WEEKLY_INGEST_DSN"], autocommit=True, connect_timeout=5
        )

    bao = os.environ["SIGNAL_WEEKLY_OPENBAO_URL"]
    recovery = OpenBaoRecoveryAuthority(bao, os.environ["SIGNAL_WEEKLY_RECOVERY_TOKEN"])
    credential = OpenBaoGitHubAppCredential(bao, os.environ["SIGNAL_WEEKLY_GITHUB_TOKEN"])
    temporal = await Client.connect(
        os.environ["SIGNAL_WEEKLY_TEMPORAL_ADDRESS"],
        namespace=os.environ["SIGNAL_WEEKLY_TEMPORAL_NAMESPACE"],
        tls=True,
    )
    with ExitStack() as stack:
        workflow = stack.enter_context(workflow_connection())
        admission = stack.enter_context(
            psycopg.connect(
                os.environ["SIGNAL_WEEKLY_ADMISSION_DSN"], autocommit=True, connect_timeout=5
            )
        )
        ingest = stack.enter_context(ingest_connection())
        writer = stack.enter_context(
            psycopg.connect(
                os.environ["SIGNAL_WEEKLY_JOURNAL_DSN"], autocommit=True, connect_timeout=5
            )
        )
        key = serialization.load_pem_private_key(
            secret_file(os.environ["SIGNAL_WEEKLY_JOURNAL_KEY"]), password=None
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("JOURNAL_KEY_INVALID")
        encryption_key = secret_file(os.environ["SIGNAL_WEEKLY_JOURNAL_ENCRYPTION_KEY"], 64)
        journal = WriteIntentJournal(writer, key.public_key(), encryption_key, key)
        fetcher = PinnedHttpFetcher(BoundedSystemResolver())

        def provider(name, origin, *, purpose="model", maximum=256 * 1024, timeout=5):
            run, policy, store = context(os.environ[name], scope, origin, maximum, timeout)
            return SharedEgressProvider(
                admission,
                ingest,
                store,
                run,
                policy,
                fetcher,
                "worker.weekly-qualification",
                OriginAdmissionPolicy(),
                None,
                purpose,
            )

        github = provider(
            "SIGNAL_WEEKLY_GITHUB_CONTEXT", "https://api.github.com", purpose="connector"
        )
        jev = provider("SIGNAL_WEEKLY_JEV_CONTEXT", "https://api.typesafe.ai")

        chat = configured_chat(scope, recovery, bao, provider)
        skills = configured_skills(
            workflow_connection,
            recovery,
            bao,
            provider,
            jev,
            evidence_connection_factory=ingest_connection,
            chat_delivery=chat,
        )
        from signal_core.internal_link_skill import InternalLinkSkillPort, internal_link_skill_io

        skills.ports["internal_link_proposals"] = InternalLinkSkillPort(
            workflow_connection,
            release_connection,
            lambda permit, guard: internal_link_skill_io(
                permit,
                guard,
                credential=credential,
                provider=github,
                runner=DockerCandidateSandbox(),
            ),
        )

        def io_factory(cycle, job):
            run, policy, store = context(
                os.environ["SIGNAL_WEEKLY_LIVE_CONTEXT"], scope, args.site_origin, 128 * 1024, 10
            )
            live = SharedLiveVerifier(
                admission,
                ingest,
                store,
                run,
                policy,
                fetcher,
                "worker.weekly-live-qualification",
                OriginAdmissionPolicy(),
            )
            return WeeklyDeliveryIO(
                credential,
                GitHubSharedEgressTransport(github),
                github,
                DockerCandidateSandbox(),
                journal,
                live,
                args.environment,
                args.deployment_actor,
            )

        delivery = WeeklyTechnicalDelivery(
            workflow_connection,
            release_connection,
            recovery,
            io_factory,
            candidate_cost_cents=args.reserved_cost_cents,
        )
        crawl = CrawlWeeklyWork(workflow_connection, ingest_connection, recovery)
        gate = AutonomyGate(
            workflow,
            recovery,
            DecisionService(
                primary=JevHttpAdapter(
                    OpenBaoJevCredential(bao, os.environ["SIGNAL_WEEKLY_JEV_TOKEN"]), jev
                ),
                recorder=PostgresDecisionRecorder(workflow, scope),
            ),
        )
        activities = WeeklyActivities(
            WeeklyCycleStore(workflow_connection),
            work=DeliveryWeeklyWork(crawl, delivery),
            candidates=delivery,
            gate=gate,
            delivery=delivery,
            skills=skills,
        )
        runtime = WeeklyDeliveryRuntime(
            temporal,
            site,
            activities,
            WeeklyScheduleReconciler(workflow, temporal, recovery),
            chat_delivery=chat,
        )
        results = await runtime.qualify_once()
        return {
            "status": "EXECUTED",
            "site_id": str(args.site_id),
            "stages": [
                {
                    "outcome": r.outcome,
                    "detail_code": r.detail_code,
                    "evidence_refs": r.evidence_refs,
                }
                for r in results
            ],
            "delivery_certified": False,
        }


def configured_chat(scope, recovery, bao, provider):
    if not os.environ.get("SIGNAL_WEEKLY_IDENTITY_DSN"):
        return None
    secrets = {}
    for channel, secret_class in (
        ("slack", OpenBaoSlackSecrets),
        ("telegram", OpenBaoTelegramSecrets),
    ):
        prefix = "SIGNAL_WEEKLY_" + channel.upper()
        if os.environ.get(prefix + "_TOKEN") and os.environ.get(prefix + "_CONTEXT"):
            secrets[channel] = secret_class(bao, os.environ[prefix + "_TOKEN"])
    if not secrets:
        return None

    def identity_connection():
        return psycopg.connect(
            os.environ["SIGNAL_WEEKLY_IDENTITY_DSN"], autocommit=True, connect_timeout=5
        )

    def egress(site, channel):
        if site != scope.site_id:
            raise PermissionError("CHAT_SCOPE_REJECTED")
        name = "telegram" if channel == "telegram" else "slack"
        if name not in secrets:
            return None
        origin = "https://api.telegram.org" if name == "telegram" else "https://slack.com"
        return provider("SIGNAL_WEEKLY_" + name.upper() + "_CONTEXT", origin, purpose="connector")

    return ChatDelivery(
        identity_connection, recovery, scope, egress, secrets.get("slack"), secrets.get("telegram")
    )


def configured_skills(
    connection_factory,
    recovery,
    bao,
    provider,
    jev,
    *,
    evidence_connection_factory=None,
    chat_delivery=None,
):
    """Optional ports use private operator configuration, never owner sessions."""
    ports = local_skill_ports(connection_factory)
    ports["chat_report_delivery"] = ChatReportSkillPort(connection_factory, chat_delivery)
    if os.environ.get("SIGNAL_WEEKLY_PSI_TOKEN") and os.environ.get("SIGNAL_WEEKLY_PSI_CONTEXT"):
        ports["pagespeed_refresh"] = PageSpeedSkillPort(
            connection_factory,
            provider(
                "SIGNAL_WEEKLY_PSI_CONTEXT",
                "https://www.googleapis.com",
                purpose="connector",
                maximum=512 * 1024,
                timeout=25,
            ),
            OpenBaoPageSpeedCredentials(
                bao,
                os.environ["SIGNAL_WEEKLY_PSI_TOKEN"],
                os.environ.get("SIGNAL_WEEKLY_PSI_KEY_PATH") or None,
            ),
        )
    ceilings = {}
    origins = {
        "openai": "https://api.openai.com",
        "perplexity": "https://api.perplexity.ai",
        "gemini": "https://generativelanguage.googleapis.com",
    }
    if os.environ.get("SIGNAL_WEEKLY_ASSISTANT_TOKEN") and evidence_connection_factory:
        for name in origins:
            prefix = "SIGNAL_WEEKLY_ASSISTANT_" + name.upper()
            if os.environ.get(prefix + "_CONTEXT") and os.environ.get(prefix + "_CEILING_MICROS"):
                ceilings[name] = int(os.environ[prefix + "_CEILING_MICROS"])
        if ceilings:

            async def assistant_egress(site, operation):
                profiles = {
                    "openai": EgressProfile.OPENAI_ASSISTANT,
                    "perplexity": EgressProfile.PERPLEXITY_ASSISTANT,
                    "gemini": EgressProfile.GEMINI_ASSISTANT,
                }
                return {
                    profiles[name]: provider(
                        "SIGNAL_WEEKLY_ASSISTANT_" + name.upper() + "_CONTEXT", origins[name]
                    )
                    for name in ceilings
                }

            ports["visibility_reobserve"] = VisibilitySkillPort(
                VisibilityActivities(
                    connection_factory,
                    evidence_connection_factory,
                    recovery,
                    credentials=OpenBaoAssistantCredentials(
                        bao, os.environ["SIGNAL_WEEKLY_ASSISTANT_TOKEN"]
                    ),
                    egress_factory=assistant_egress,
                    ceilings=ceilings,
                )
            )
    for name, secret_class, profiles in (
        (
            "gsc",
            OpenBaoGscSecrets,
            (
                (EgressProfile.GOOGLE_OAUTH_TOKEN, "https://oauth2.googleapis.com"),
                (EgressProfile.GSC_API, "https://www.googleapis.com"),
            ),
        ),
        (
            "bing",
            OpenBaoBingSecrets,
            (
                (EgressProfile.BING_OAUTH_TOKEN, "https://www.bing.com"),
                (EgressProfile.BING_API, "https://www.bing.com"),
            ),
        ),
        (
            "ga4",
            OpenBaoGa4Secrets,
            (
                (EgressProfile.GOOGLE_OAUTH_TOKEN, "https://oauth2.googleapis.com"),
                (EgressProfile.GA4_DATA, "https://analyticsdata.googleapis.com"),
            ),
        ),
    ):
        prefix = "SIGNAL_WEEKLY_" + name.upper()
        names = (prefix + "_TOKEN", prefix + "_TOKEN_CONTEXT", prefix + "_DATA_CONTEXT")
        if all(os.environ.get(key) for key in names):
            gateways = {
                profile: provider(context_name, origin, purpose="connector")
                for (profile, origin), context_name in zip(profiles, names[1:], strict=True)
            }
            ports["import_" + name] = ImportSkillPort(
                connection_factory, gateways, secret_class(bao, os.environ[names[0]]), name
            )
    brain_names = (
        "SIGNAL_WEEKLY_MODEL_TOKEN",
        "SIGNAL_WEEKLY_MODEL_CONTEXT",
        "SIGNAL_WEEKLY_BRAIN_ARTIFACT_ROOT",
        "SIGNAL_WEEKLY_BRAIN_KEY_FILE",
        "SIGNAL_WEEKLY_BRAIN_KEY_REF",
        "SIGNAL_WEEKLY_BRAIN_COST_BOUND_CENTS",
    )
    bound = 0
    if all(os.environ.get(key) for key in brain_names):
        root = Path(os.environ[brain_names[2]])
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("BRAIN_ARTIFACT_ROOT_INVALID")
        key = ArtifactEncryptionKey(
            os.environ[brain_names[4]], secret_file(os.environ[brain_names[3]], 32)
        )
        bound = int(os.environ[brain_names[5]])
        ports["brain_refresh"] = BrainSkillPort(
            connection_factory,
            BusinessBrainExtractor(
                EncryptedLocalArtifactStore(root),
                key,
                BusinessBrainModelAdapter(
                    OpenBaoModelCredential(bao, os.environ[brain_names[0]]),
                    provider(brain_names[1], "https://api.openai.com"),
                ),
                JevHttpAdapter(
                    OpenBaoJevCredential(bao, os.environ["SIGNAL_WEEKLY_JEV_TOKEN"]), jev
                ),
                connection_factory,
            ),
        )
    return WeeklySkills(connection_factory, recovery, ports=ports, brain_cost_bound_cents=bound)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--tenant-id", type=UUID)
    parser.add_argument("--site-id", type=UUID)
    parser.add_argument("--grant-id", type=UUID)
    parser.add_argument("--site-origin")
    parser.add_argument("--environment", default="production")
    parser.add_argument("--deployment-actor", type=int)
    parser.add_argument("--reserved-cost-cents", type=int, default=25)
    args = parser.parse_args()
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if (
        not args.execute
        or missing
        or not all(
            (args.tenant_id, args.site_id, args.grant_id, args.site_origin, args.deployment_actor)
        )
    ):
        print(
            json.dumps(
                {
                    "status": "NOT_EXECUTED",
                    "missing_configuration": missing,
                    "reason": "Explicit execution and qualified owner resources required.",
                }
            )
        )
        return 2
    try:
        print(json.dumps(asyncio.run(qualify(args))))
        return 0
    except (Exception, KeyboardInterrupt):
        # Provider payloads and connection strings never enter qualification output.
        print(
            json.dumps({"status": "UNAVAILABLE", "reason": "WEEKLY_LIVE_QUALIFICATION_UNAVAILABLE"})
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
