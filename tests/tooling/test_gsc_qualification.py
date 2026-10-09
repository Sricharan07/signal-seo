import os
import subprocess
import sys
from pathlib import Path


def test_live_gsc_qualification_requires_shared_egress_before_prompting():
    root = Path(__file__).resolve().parents[2]
    env = {key: value for key, value in os.environ.items() if not key.startswith("SIGNAL_GSC_")}
    result = subprocess.run(
        [sys.executable, "scripts/qualify_gsc_live.py"],
        input="",
        text=True,
        capture_output=True,
        env=env,
        cwd=root,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "GSC_QUALIFICATION_UNCONFIGURED"
