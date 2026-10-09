import asyncio
import sys
from argparse import Namespace
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from qualify_github_pr_extension import qualify  # noqa: E402
from qualify_github_read_binding import QualificationConfigurationError  # noqa: E402


def test_live_pr_qualification_requires_dedicated_provider_context(monkeypatch):
    monkeypatch.delenv("SIGNAL_GITHUB_EGRESS_CONTEXT", raising=False)
    monkeypatch.delenv("SIGNAL_GITHUB_OPENBAO_URL", raising=False)
    args = Namespace(
        site_id=uuid4(),
        binding_id=uuid4(),
        idempotency_key=uuid4(),
        recovery_generation="current",
    )
    with pytest.raises(QualificationConfigurationError, match="PROVIDER_UNCONFIGURED"):
        asyncio.run(qualify(args, "not-used", "not-used"))
