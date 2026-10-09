#!/usr/bin/env python3
"""Run the invocation-owned workflow consumer OCI image lab."""

import signal
import subprocess
import sys

from workflow_consumer_image_lab import LabError, main


def interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Consumer image lab interrupted; cleanup was attempted.", file=sys.stderr)
        raise SystemExit(130) from None
    except (LabError, subprocess.SubprocessError) as error:
        print(f"Consumer image lab failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
