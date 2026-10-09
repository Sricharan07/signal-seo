"""Keep gate artifacts in an invocation-private root; standalone labs retain their paths."""

import os
from pathlib import Path


def runtime_root(root: Path) -> Path:
    configured = os.environ.get("SIGNAL_LAB_RUNTIME_ROOT")
    return Path(configured) if configured else root / ".runtime"
