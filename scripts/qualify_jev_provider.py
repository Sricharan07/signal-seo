"""One-shot live TypeSafe qualification; reads the key from stdin and never stores it."""

import asyncio
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.crawl_admission import OriginAdmissionPolicy  # noqa: E402
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore  # noqa: E402
from signal_core.crawl_frontier import CrawlRunOpened  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver, PinnedHttpFetcher  # noqa: E402
from signal_core.crawl_urls import CrawlScopePolicy  # noqa: E402
from signal_core.decision_contracts import (  # noqa: E402
    ChoiceQuestion,
    DecisionRequest,
    NoulQuestion,
    Recommendation,
    ScoreQuestion,
)
from signal_core.jev_decisions import JevHttpAdapter, JevUnavailable  # noqa: E402
from signal_core.shared_egress import SharedEgressProvider  # noqa: E402

JEV_ORIGIN = "https://api.typesafe.ai"


class QualificationConfigurationError(RuntimeError):
    """Live qualification lacks a valid pre-created shared-egress authority."""


class StdinCredential:
    def __init__(self, key: str) -> None:
        if (
            not 16 <= len(key) <= 512
            or not key.isascii()
            or any(character.isspace() for character in key)
        ):
            raise ValueError("The TypeSafe key is not a bounded bearer credential.")
        self._key = key

    async def api_key(self, **kwargs) -> str:
        return self._key


async def qualify(key: str, egress: SharedEgressProvider) -> dict[str, object]:
    decision = DecisionRequest(
        decision_id=uuid4(),
        purpose="provider.qualification",
        state={"kind": "synthetic_qualification", "customer_data": False},
        questions={
            "recommendation": ChoiceQuestion(
                "Choose the safest action for a synthetic provider qualification.",
                {
                    "ship": "Continue this synthetic qualification.",
                    "ask_owner": "Ask the owner for review.",
                    "reject": "Reject this synthetic qualification.",
                },
            ),
            "clarity": ScoreQuestion(
                "Rate how clear the synthetic state is.",
                ["unclear", "partly clear", "clear"],
            ),
            "is_synthetic": NoulQuestion("Is this explicitly synthetic test data?"),
        },
        threshold=0.8,
        policy_ceiling=Recommendation.ASK_OWNER,
    )
    evaluation = await JevHttpAdapter(StdinCredential(key), egress).evaluate(decision)
    return {
        "status": "PASS",
        "model_reported": evaluation.model_reported,
        "answer_types": {
            question_id: answer.type for question_id, answer in evaluation.answers.items()
        },
        "usage": {
            "input_tokens": evaluation.input_tokens,
            "output_tokens": evaluation.output_tokens,
        },
        "customer_data": False,
        "credential_recorded": False,
        "authority_created": False,
    }


@contextmanager
def configured_egress():
    context_path = os.environ.get("SIGNAL_JEV_EGRESS_CONTEXT")
    admission_dsn = os.environ.get("SIGNAL_JEV_EGRESS_ADMISSION_DSN")
    ingest_dsn = os.environ.get("SIGNAL_JEV_EGRESS_INGEST_DSN")
    if not context_path or not admission_dsn or not ingest_dsn:
        raise QualificationConfigurationError("JEV_EGRESS_UNCONFIGURED")
    document = _read_context(Path(context_path))
    run = _run(document["run"])
    policy = _policy(document["policy"])
    store = EncryptedLocalArtifactStore(Path(document["artifact_root"]))
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
            "worker.jev-qualification",
            OriginAdmissionPolicy(),
            None,
        )
    except psycopg.Error:
        raise QualificationConfigurationError("JEV_EGRESS_STATE_UNAVAILABLE") from None
    finally:
        if admission is not None:
            admission.close()
        if ingest is not None:
            ingest.close()


def _read_context(path: Path) -> dict[str, object]:
    try:
        if not path.is_absolute() or path.stat().st_size > 64 * 1024:
            raise ValueError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise QualificationConfigurationError("JEV_EGRESS_CONTEXT_INVALID") from None
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "artifact_root",
        "run",
        "policy",
    }:
        raise QualificationConfigurationError("JEV_EGRESS_CONTEXT_INVALID")
    artifact_root = document["artifact_root"]
    if (
        document["schema_version"] != 1
        or not isinstance(artifact_root, str)
        or not 1 <= len(artifact_root) <= 4096
        or not Path(artifact_root).is_absolute()
    ):
        raise QualificationConfigurationError("JEV_EGRESS_CONTEXT_INVALID")
    return document


def _run(value: object) -> CrawlRunOpened:
    fields = {
        "tenant_id",
        "site_id",
        "command_id",
        "run_id",
        "root_frontier_id",
        "root_url_id",
        "first_run_id",
        "started_at",
        "status",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise QualificationConfigurationError("JEV_EGRESS_CONTEXT_INVALID")
    try:
        started_at = datetime.fromisoformat(value["started_at"])
        run = CrawlRunOpened(
            tenant_id=UUID(value["tenant_id"]),
            site_id=UUID(value["site_id"]),
            command_id=UUID(value["command_id"]),
            run_id=UUID(value["run_id"]),
            root_frontier_id=UUID(value["root_frontier_id"]),
            root_url_id=UUID(value["root_url_id"]),
            first_run_id=value["first_run_id"],
            started_at=started_at,
            status=value["status"],
            duplicate=False,
        )
    except (TypeError, ValueError):
        raise QualificationConfigurationError("JEV_EGRESS_CONTEXT_INVALID") from None
    if (
        not isinstance(run.first_run_id, str)
        or not run.first_run_id
        or run.started_at.tzinfo is None
        or run.status != "running"
    ):
        raise QualificationConfigurationError("JEV_EGRESS_CONTEXT_INVALID")
    return run


def _policy(value: object) -> CrawlScopePolicy:
    fields = {
        "schema_version",
        "allowed_origins",
        "user_agent",
        "max_redirects",
        "max_body_bytes",
        "request_timeout_seconds",
        "total_timeout_seconds",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise QualificationConfigurationError("JEV_EGRESS_CONTEXT_INVALID")
    try:
        policy = CrawlScopePolicy(
            schema_version=value["schema_version"],
            allowed_origins=tuple(value["allowed_origins"]),
            user_agent=value["user_agent"],
            max_redirects=value["max_redirects"],
            max_body_bytes=value["max_body_bytes"],
            request_timeout_seconds=value["request_timeout_seconds"],
            total_timeout_seconds=value["total_timeout_seconds"],
        )
    except (TypeError, ValueError):
        raise QualificationConfigurationError("JEV_EGRESS_CONTEXT_INVALID") from None
    if policy.allowed_origins != (JEV_ORIGIN,):
        raise QualificationConfigurationError("JEV_EGRESS_CONTEXT_INVALID")
    return policy


def main() -> int:
    key = sys.stdin.read(513)
    if len(key) > 512:
        print("Jev live qualification failed (CREDENTIAL_TOO_LARGE).", file=sys.stderr)
        return 1
    try:
        with configured_egress() as egress:
            report = asyncio.run(qualify(key, egress))
    except JevUnavailable as error:
        print(f"Jev live qualification failed ({error.code}).", file=sys.stderr)
        return 1
    except QualificationConfigurationError as error:
        print(f"Jev live qualification failed ({error}).", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Jev live qualification failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    finally:
        key = ""
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
