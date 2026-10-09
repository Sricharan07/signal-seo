#!/usr/bin/env python3
"""Run the disposable joint PostgreSQL and Temporal consumer lab."""

import signal
import subprocess
import sys

import psycopg
from consumer_lab import LabError, main


def interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Consumer lab interrupted; invocation-owned cleanup was attempted.", file=sys.stderr)
        raise SystemExit(130) from None
    except (LabError, subprocess.SubprocessError, psycopg.Error) as error:
        print(f"Consumer lab failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
