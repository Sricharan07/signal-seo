"""One-shot synthetic search against one official provider through shared egress."""

import asyncio
import getpass
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from qualify_jev_provider import QualificationConfigurationError, _read_context, _run  # noqa: E402
from signal_core.assistant_providers import (  # noqa: E402
    AssistantProviderError,
    request_assistant_search,
)
from signal_core.crawl_admission import OriginAdmissionPolicy  # noqa: E402
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver, PinnedHttpFetcher  # noqa: E402
from signal_core.crawl_urls import CrawlScopePolicy  # noqa: E402
from signal_core.shared_egress import SharedEgressProvider  # noqa: E402

ORIGINS = {
    "openai": "https://api.openai.com",
    "perplexity": "https://api.perplexity.ai",
    "gemini": "https://generativelanguage.googleapis.com",
}


class QualificationUnavailable(RuntimeError):
    pass


class OneShotCredential:
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def api_key(self, provider: str) -> str:
        return self._api_key


@contextmanager
def configured_egress(provider: str):
    context_path = os.environ.get("SIGNAL_ASSISTANT_EGRESS_CONTEXT")
    admission_dsn = os.environ.get("SIGNAL_ASSISTANT_EGRESS_ADMISSION_DSN")
    ingest_dsn = os.environ.get("SIGNAL_ASSISTANT_EGRESS_INGEST_DSN")
    if not context_path or not admission_dsn or not ingest_dsn:
        raise QualificationUnavailable("ASSISTANT_EGRESS_UNCONFIGURED")
    try:
        document = _read_context(Path(context_path))
        run = _run(document["run"])
        values = document["policy"]
        if not isinstance(values, dict) or set(values) != {
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
            schema_version=values["schema_version"],
            allowed_origins=tuple(values["allowed_origins"]),
            user_agent=values["user_agent"],
            max_redirects=values["max_redirects"],
            max_body_bytes=values["max_body_bytes"],
            request_timeout_seconds=values["request_timeout_seconds"],
            total_timeout_seconds=values["total_timeout_seconds"],
        )
        if policy.allowed_origins != (ORIGINS[provider],):
            raise ValueError
        store = EncryptedLocalArtifactStore(Path(document["artifact_root"]))
    except (KeyError, TypeError, ValueError, QualificationConfigurationError):
        raise QualificationUnavailable("ASSISTANT_EGRESS_CONTEXT_INVALID") from None
    admission = ingest = None
    try:
        admission = psycopg.connect(admission_dsn, autocommit=True, connect_timeout=5)
        ingest = psycopg.connect(ingest_dsn, autocommit=True, connect_timeout=5)
        yield SharedEgressProvider(
            admission,
            ingest,
            store,
            run,
            policy,
            PinnedHttpFetcher(BoundedSystemResolver()),
            "worker.assistant-qualification",
            OriginAdmissionPolicy(),
            None,
            purpose="model",
        )
    except psycopg.Error:
        raise QualificationUnavailable("ASSISTANT_EGRESS_STATE_UNAVAILABLE") from None
    finally:
        if admission is not None:
            admission.close()
        if ingest is not None:
            ingest.close()


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ORIGINS:
        print("Usage: qualify_assistants_live.py {openai|perplexity|gemini}", file=sys.stderr)
        return 2
    provider = sys.argv[1]
    if not all(
        os.environ.get(name)
        for name in (
            "SIGNAL_ASSISTANT_EGRESS_CONTEXT",
            "SIGNAL_ASSISTANT_EGRESS_ADMISSION_DSN",
            "SIGNAL_ASSISTANT_EGRESS_INGEST_DSN",
        )
    ):
        print("ASSISTANT_QUALIFICATION_UNCONFIGURED", file=sys.stderr)
        return 2
    api_key = getpass.getpass(f"{provider} API key: ")
    try:
        if not 16 <= len(api_key) <= 512 or any(
            ord(char) < 33 or ord(char) > 126 for char in api_key
        ):
            raise QualificationUnavailable("ASSISTANT_KEY_INVALID")
        with configured_egress(provider) as egress:
            result = asyncio.run(
                request_assistant_search(
                    OneShotCredential(api_key),
                    egress,
                    provider=provider,
                    question="What is a sitemap and why is it useful?",
                    operation_id=uuid4(),
                )
            )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "provider": provider,
                    "model_reported": result.model_reported,
                    "response_received": True,
                    "credential_recorded": False,
                }
            )
        )
        return 0
    except (AssistantProviderError, QualificationUnavailable) as error:
        print(error.code if hasattr(error, "code") else str(error), file=sys.stderr)
        return 1
    finally:
        api_key = ""


if __name__ == "__main__":
    raise SystemExit(main())
