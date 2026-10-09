"""Replay the frozen synthetic oracle against the pre-refactor and current validators."""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/control_plane/src"))


def main():
    from signal_core import egress_profiles

    from tests.connectors.test_egress_declarations import _CASES, _decode

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{7,40}", args.baseline) is None:
        parser.error("Baseline must be a commit hash.")
    source = subprocess.check_output(
        [
            "git",
            "show",
            f"{args.baseline}:services/control_plane/src/signal_core/egress_profiles.py",
        ],
        cwd=ROOT,
    )
    before = ModuleType("signal_egress_before_0167")
    sys.modules[before.__name__] = before
    # This is trusted repository Python, never provider or uploaded content.
    exec(compile(source, f"{args.baseline}:egress_profiles.py", "exec"), before.__dict__)
    passed = 0
    for index, case in enumerate(_CASES):
        for label, module in (("before", before), ("after", egress_profiles)):
            try:
                module.validate_profile_request(**_decode(case["arguments"], module))
                allowed = True
            except ValueError:
                allowed = False
            if allowed != case["allowed"]:
                raise AssertionError(
                    f"Oracle decision changed: {label} case {index} ({case['source']})"
                )
        passed += 1
    print(f"PASS {passed} before/after decisions; no widened or narrowed request cases")


if __name__ == "__main__":
    main()
