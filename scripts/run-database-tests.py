"""Entrypoint for the disposable database lab; never accepts an external DSN."""

import signal
import subprocess
import sys

import psycopg
from database_lab import LabError, main


def interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(
            "Database tests interrupted; invocation-owned cleanup was attempted.", file=sys.stderr
        )
        sys.exit(130)
    except LabError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, subprocess.SubprocessError, psycopg.Error) as error:
        # Do not render exceptions containing credential-bearing arguments or DSNs.
        print(
            f"Database tests failed ({type(error).__name__}); no production DB was used.",
            file=sys.stderr,
        )
        sys.exit(1)
