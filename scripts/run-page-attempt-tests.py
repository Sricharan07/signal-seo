"""Entrypoint for the joint page-attempt lab; never accepts external infrastructure."""

import signal
import subprocess
import sys

import psycopg
from crawler_network_lab import LabError as NetworkLabError
from database_lab import LabError as DatabaseLabError
from page_attempt_lab import LabError, main


def interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Page-attempt tests interrupted; owned cleanup was attempted.", file=sys.stderr)
        sys.exit(130)
    except (LabError, NetworkLabError, DatabaseLabError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, subprocess.SubprocessError, psycopg.Error) as error:
        print(
            f"Page-attempt tests failed ({type(error).__name__}); no production system was used.",
            file=sys.stderr,
        )
        sys.exit(1)
