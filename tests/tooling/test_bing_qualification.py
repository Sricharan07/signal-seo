import subprocess
import sys


def test_live_qualification_stays_unconfigured_without_credentials():
    result = subprocess.run(
        [sys.executable, "scripts/qualify_bing_live.py"],
        env={},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "BING_QUALIFICATION_UNCONFIGURED"
