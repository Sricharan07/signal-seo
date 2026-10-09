"""Fixed synthetic evaluation; live runs require an operator-composed, budgeted gateway."""

import argparse
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.article_pipeline import draft_with_quality  # noqa: E402
from signal_core.model_budget import PostgresModelBudget  # noqa: E402
from signal_core.model_credentials import OpenBaoModelCredential  # noqa: E402
from signal_core.model_reasoning import ContentWriterModelAdapter, MetadataDraftError  # noqa: E402
from signal_core.shared_egress import SharedEgressProvider  # noqa: E402
from signal_core.writing_evaluation import CASES, evaluation_packet  # noqa: E402


async def evaluate(model, *, live):
    results = []
    run_id = str(uuid4()) if live else "synthetic-fixed"
    for case in CASES:
        result, quality, receipts = await draft_with_quality(
            model,
            evaluation_packet(case),
            uuid5(NAMESPACE_URL, "writing-0136-eval:" + run_id + ":" + case["name"]),
        )
        results.append(
            {
                "case": case["name"],
                "quality": quality,
                "passes": receipts,
                "output_sha256": result.output_sha256,
            }
        )
    print(
        json.dumps(
            {
                "state": "EXECUTED",
                "mode": "live" if live else "synthetic_test_double",
                "production_authority": False,
                "cases": results,
            },
            ensure_ascii=True,
        )
    )
    return int(any(r["quality"]["state"] != "passed" for r in results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live:
        factory = os.environ.get("SIGNAL_WRITING_EVAL_RUNTIME_FACTORY")
        if not factory:
            print(
                json.dumps({"state": "NOT_EXECUTED", "reason": "OWNER_MODEL_RUNTIME_UNCONFIGURED"})
            )
            return 0
        if os.environ.get("SIGNAL_WRITING_EVAL_SYNTHETIC_ONLY") != "1":
            print(
                json.dumps(
                    {"state": "NOT_EXECUTED", "reason": "DEDICATED_SYNTHETIC_SCOPE_REQUIRED"}
                )
            )
            return 1
        module, separator, name = factory.partition(":")
        if not separator or not name.isidentifier():
            raise ValueError("Invalid operator runtime factory.")
        # The operator composes existing OpenBao, shared egress and PostgreSQL ports.
        # No direct HTTP, raw key, customer brief, write tool or budget bypass exists here.
        model = getattr(importlib.import_module(module), name)()
        if (
            not isinstance(model, ContentWriterModelAdapter)
            or not model.configured
            or not isinstance(model.reasoner.budget, PostgresModelBudget)
            or not isinstance(model.reasoner.egress, SharedEgressProvider)
            or not isinstance(model.reasoner.credential, OpenBaoModelCredential)
        ):
            print(
                json.dumps({"state": "NOT_EXECUTED", "reason": "OWNER_MODEL_RUNTIME_UNCONFIGURED"})
            )
            return 1
    else:
        from tests.tooling.test_writing_quality import model_and_budget

        model, _, _ = model_and_budget()
    return asyncio.run(evaluate(model, live=args.live))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MetadataDraftError as error:
        print(json.dumps({"state": "unavailable", "reason": error.code}))
        sys.exit(1)
    except (ValueError, ImportError, AttributeError):
        print(json.dumps({"state": "unavailable", "reason": "EVALUATION_RUNTIME_INVALID"}))
        sys.exit(1)
