"""Qualify one existing GitHub binding for PR permission and repository format."""

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from qualify_github_read_binding import (  # noqa: E402
    QualificationConfigurationError,
    _connections,
    _context,
)
from signal_core.crawl_admission import OriginAdmissionPolicy  # noqa: E402
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver, PinnedHttpFetcher  # noqa: E402
from signal_core.github_pr_extension import (  # noqa: E402
    GitHubPrExtensionUnavailable,
    inspect_current_github_pr_extension,
    observe_github_pr_extension,
)
from signal_core.github_read_binding import (  # noqa: E402
    GitHubBindingUnavailable,
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
)
from signal_core.shared_egress import SharedEgressProvider  # noqa: E402


async def qualify(args: argparse.Namespace, session_token: str, bao_token: str) -> dict:
    context_path = os.environ.get("SIGNAL_GITHUB_EGRESS_CONTEXT")
    bao_url = os.environ.get("SIGNAL_GITHUB_OPENBAO_URL")
    if not context_path or not bao_url:
        raise QualificationConfigurationError("GITHUB_PROVIDER_UNCONFIGURED")
    run, policy, artifact_root = _context(Path(context_path))
    if run.site_id != args.site_id:
        raise QualificationConfigurationError("GITHUB_SITE_CONTEXT_MISMATCH")
    credential = OpenBaoGitHubAppCredential(bao_url, bao_token)
    with _connections() as (identity, admission, ingest):
        provider = SharedEgressProvider(
            admission,
            ingest,
            EncryptedLocalArtifactStore(artifact_root),
            run,
            policy,
            PinnedHttpFetcher(BoundedSystemResolver()),
            "worker.github-pr-qualification",
            OriginAdmissionPolicy(),
            None,
            "connector",
        )
        transport = GitHubSharedEgressTransport(provider)
        extension = await observe_github_pr_extension(
            identity,
            session_token=session_token,
            current_recovery_generation=args.recovery_generation,
            site_id=args.site_id,
            binding_id=args.binding_id,
            idempotency_key=args.idempotency_key,
            credential=credential,
            github_transport=transport,
        )
        if not extension.candidate_compatible:
            raise GitHubPrExtensionUnavailable("GITHUB_REPOSITORY_FORMAT_UNSUPPORTED")
        snapshot = await inspect_current_github_pr_extension(
            identity,
            session_token=session_token,
            current_recovery_generation=args.recovery_generation,
            site_id=args.site_id,
            extension_id=extension.id,
            credential=credential,
            github_transport=transport,
        )
    return {
        "status": "PASS",
        "extension_id": str(extension.id),
        "binding_id": str(extension.binding_id),
        "site_id": str(extension.site_id),
        "repository_id": snapshot.repository_id,
        "base_sha": snapshot.base_sha,
        "tree_sha": extension.tree_sha,
        "framework": extension.framework,
        "content_format": extension.content_format,
        "content_sha": extension.content_sha,
        "coverage": extension.coverage,
        "external_repository_write": False,
        "pull_request_created": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-id", type=UUID, required=True)
    parser.add_argument("--binding-id", type=UUID, required=True)
    parser.add_argument("--idempotency-key", type=UUID, required=True)
    parser.add_argument("--recovery-generation", required=True)
    args = parser.parse_args()
    session_token = getpass.getpass("Owner session token: ")
    bao_token = getpass.getpass("OpenBao read token: ")
    try:
        result = asyncio.run(qualify(args, session_token, bao_token))
    except Exception as error:
        if isinstance(error, QualificationConfigurationError):
            code = error.args[0]
        elif isinstance(error, (GitHubBindingUnavailable, GitHubPrExtensionUnavailable)):
            code = error.code
        else:
            code = type(error).__name__
        print(json.dumps({"status": "FAIL", "code": code}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
