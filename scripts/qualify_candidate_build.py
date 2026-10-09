"""Qualify one exact GitHub base revision in the isolated candidate sandbox."""

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
from signal_core.candidate_build_service import (  # noqa: E402
    CandidateBuildConflict,
    CandidateBuildUnavailable,
    build_candidate_from_github,
)
from signal_core.candidate_sandbox import (  # noqa: E402
    CandidateSandboxUnavailable,
    DockerCandidateSandbox,
)
from signal_core.crawl_admission import OriginAdmissionPolicy  # noqa: E402
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver, PinnedHttpFetcher  # noqa: E402
from signal_core.github_read_binding import (  # noqa: E402
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
            "worker.candidate-build-qualification",
            OriginAdmissionPolicy(),
            None,
            "connector",
        )
        receipt = await build_candidate_from_github(
            identity,
            session_token=session_token,
            current_recovery_generation=args.recovery_generation,
            site_id=args.site_id,
            extension_id=args.extension_id,
            idempotency_key=args.idempotency_key,
            credential=credential,
            github_transport=GitHubSharedEgressTransport(provider),
            runner=DockerCandidateSandbox(),
        )
    return {
        "status": "PASS" if receipt.exit_class == "passed" else "BUILD_FAILED",
        "build_id": str(receipt.id),
        "site_id": str(receipt.site_id),
        "base_sha": receipt.base_sha,
        "patch_sha256": receipt.patch_sha256,
        "toolchain": receipt.toolchain,
        "command": receipt.command,
        "exit_class": receipt.exit_class,
        "logs_sha256": receipt.logs_sha256,
        "artifacts": [
            {"path": path, "sha256": digest, "size": size}
            for path, digest, size in receipt.artifacts
        ],
        "external_repository_write": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-id", type=UUID, required=True)
    parser.add_argument("--extension-id", type=UUID, required=True)
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
        elif isinstance(error, (CandidateBuildConflict, CandidateBuildUnavailable)):
            code = getattr(error, "code", "CANDIDATE_BUILD_CONFLICT")
        elif isinstance(error, CandidateSandboxUnavailable):
            code = error.code
        else:
            code = type(error).__name__
        print(json.dumps({"status": "FAIL", "code": code}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
