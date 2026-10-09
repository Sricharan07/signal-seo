"""Qualify an exact dedicated GitHub App binding through current shared egress."""

import argparse
import asyncio
import getpass
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.crawl_admission import OriginAdmissionPolicy  # noqa: E402
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore  # noqa: E402
from signal_core.crawl_frontier import CrawlRunOpened  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver, PinnedHttpFetcher  # noqa: E402
from signal_core.crawl_urls import CrawlScopePolicy  # noqa: E402
from signal_core.github_app import GitHubRepositoryTarget  # noqa: E402
from signal_core.github_read_binding import (  # noqa: E402
    GitHubBindingUnavailable,
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
    bind_github_read_repository,
    inspect_current_github_read_binding,
)
from signal_core.shared_egress import SharedEgressProvider  # noqa: E402


class QualificationConfigurationError(Exception):
    """Live qualification lacks an exact, pre-created provider context."""


def _context(path: Path) -> tuple[CrawlRunOpened, CrawlScopePolicy, Path]:
    try:
        if not path.is_absolute() or path.stat().st_size > 64 * 1024:
            raise ValueError
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(document, dict)
            or set(document) != {"schema_version", "artifact_root", "run", "policy"}
            or document["schema_version"] != 1
        ):
            raise ValueError
        raw_run = document["run"]
        if not isinstance(raw_run, dict) or set(raw_run) != {
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
            raise ValueError
        run = CrawlRunOpened(
            tenant_id=UUID(raw_run["tenant_id"]),
            site_id=UUID(raw_run["site_id"]),
            command_id=UUID(raw_run["command_id"]),
            run_id=UUID(raw_run["run_id"]),
            root_frontier_id=UUID(raw_run["root_frontier_id"]),
            root_url_id=UUID(raw_run["root_url_id"]),
            first_run_id=raw_run["first_run_id"],
            started_at=datetime.fromisoformat(raw_run["started_at"]),
            status=raw_run["status"],
            duplicate=False,
        )
        raw_policy = document["policy"]
        if not isinstance(raw_policy, dict) or set(raw_policy) != {
            "schema_version",
            "allowed_origins",
            "user_agent",
            "max_redirects",
            "max_body_bytes",
            "request_timeout_seconds",
            "total_timeout_seconds",
        }:
            raise ValueError
        policy = CrawlScopePolicy(
            schema_version=raw_policy["schema_version"],
            allowed_origins=tuple(raw_policy["allowed_origins"]),
            user_agent=raw_policy["user_agent"],
            max_redirects=raw_policy["max_redirects"],
            max_body_bytes=raw_policy["max_body_bytes"],
            request_timeout_seconds=raw_policy["request_timeout_seconds"],
            total_timeout_seconds=raw_policy["total_timeout_seconds"],
        )
        artifact_root = Path(document["artifact_root"])
        if (
            run.status != "running"
            or run.started_at.tzinfo is None
            or not isinstance(run.first_run_id, str)
            or not run.first_run_id
            or policy.allowed_origins != ("https://api.github.com",)
            or policy.user_agent != "SignalBot/1.0 (+https://signal.example/bot)"
            or policy.max_redirects != 0
            or policy.max_body_bytes > 256 * 1024
            or policy.request_timeout_seconds > 5
            or policy.total_timeout_seconds > 15
            or not artifact_root.is_absolute()
        ):
            raise ValueError
        return run, policy, artifact_root
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise QualificationConfigurationError("GITHUB_EGRESS_CONTEXT_INVALID") from None


@contextmanager
def _connections():
    names = (
        "SIGNAL_GITHUB_IDENTITY_DSN",
        "SIGNAL_GITHUB_EGRESS_ADMISSION_DSN",
        "SIGNAL_GITHUB_EGRESS_INGEST_DSN",
    )
    if any(not os.environ.get(name) for name in names):
        raise QualificationConfigurationError("GITHUB_DATABASE_UNCONFIGURED")
    opened = []
    try:
        for name in names:
            opened.append(psycopg.connect(os.environ[name], autocommit=True, connect_timeout=5))
        yield tuple(opened)
    except psycopg.Error:
        raise QualificationConfigurationError("GITHUB_DATABASE_UNAVAILABLE") from None
    finally:
        for connection in opened:
            connection.close()


async def qualify(args: argparse.Namespace, session_token: str, bao_token: str) -> dict:
    context_path = os.environ.get("SIGNAL_GITHUB_EGRESS_CONTEXT")
    bao_url = os.environ.get("SIGNAL_GITHUB_OPENBAO_URL")
    if not context_path or not bao_url:
        raise QualificationConfigurationError("GITHUB_PROVIDER_UNCONFIGURED")
    run, policy, artifact_root = _context(Path(context_path))
    if run.site_id != args.site_id:
        raise QualificationConfigurationError("GITHUB_SITE_CONTEXT_MISMATCH")
    target = GitHubRepositoryTarget(
        installation_id=args.installation_id,
        owner=args.owner,
        repository=args.repository,
        base_branch=args.base_branch,
        content_path=args.content_path,
    )
    credential = OpenBaoGitHubAppCredential(bao_url, bao_token)
    with _connections() as (identity, admission, ingest):
        provider = SharedEgressProvider(
            admission,
            ingest,
            EncryptedLocalArtifactStore(artifact_root),
            run,
            policy,
            PinnedHttpFetcher(BoundedSystemResolver()),
            "worker.github-qualification",
            OriginAdmissionPolicy(),
            None,
            "connector",
        )
        transport = GitHubSharedEgressTransport(provider)
        binding = await bind_github_read_repository(
            identity,
            session_token=session_token,
            current_recovery_generation=args.recovery_generation,
            site_id=args.site_id,
            idempotency_key=args.idempotency_key,
            target=target,
            credential=credential,
            github_transport=transport,
        )
        snapshot = await inspect_current_github_read_binding(
            identity,
            session_token=session_token,
            current_recovery_generation=args.recovery_generation,
            site_id=args.site_id,
            binding_id=binding.id,
            credential=credential,
            github_transport=transport,
        )
    return {
        "status": "PASS",
        "binding_id": str(binding.id),
        "site_id": str(binding.site_id),
        "installation_id": snapshot.installation_id,
        "repository_id": snapshot.repository_id,
        "full_name": snapshot.full_name,
        "base_branch": snapshot.base_branch,
        "base_sha": snapshot.base_sha,
        "protected": snapshot.protected,
        "external_write": False,
        "merge_or_deploy_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-id", type=UUID, required=True)
    parser.add_argument("--idempotency-key", type=UUID, required=True)
    parser.add_argument("--installation-id", type=int, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--content-path", required=True)
    parser.add_argument("--recovery-generation", required=True)
    args = parser.parse_args()
    session_token = getpass.getpass("Owner session token: ")
    bao_token = getpass.getpass("OpenBao read token: ")
    try:
        result = asyncio.run(qualify(args, session_token, bao_token))
    except Exception as error:
        if isinstance(error, QualificationConfigurationError):
            code = error.args[0]
        elif isinstance(error, GitHubBindingUnavailable):
            code = error.code
        else:
            code = type(error).__name__
        print(json.dumps({"status": "FAIL", "code": code}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
