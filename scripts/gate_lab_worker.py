"""Run existing lab entrypoints with graceful cancellation through their finally blocks."""

import runpy
import signal
import sys
from pathlib import Path


def interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    sys.argv = sys.argv[1:]
    sys.argv[0] = str(Path(sys.argv[0]).resolve())
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
    except KeyboardInterrupt:
        raise SystemExit(130) from None
