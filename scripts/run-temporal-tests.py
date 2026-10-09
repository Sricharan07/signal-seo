#!/usr/bin/env python3
"""Run the disposable Temporal integration lab."""

import subprocess
import sys

from temporal_lab import LabError, main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LabError, subprocess.TimeoutExpired) as error:
        print(f"Temporal lab failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
