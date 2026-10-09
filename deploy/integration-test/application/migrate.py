"""One explicitly invoked migration job; credentials are never shell arguments."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from psycopg.conninfo import make_conninfo

os.environ["SIGNAL_MIGRATION_DSN"] = make_conninfo(
    host="application-database",
    dbname="signal",
    user="signal_migrator",
    password=Path("/run/signal-migration/migrator-password").read_text(),
    sslmode="verify-full",
    sslrootcert="/run/signal-tls/ca.pem",
    connect_timeout=5,
)
try:
    command.upgrade(Config("database/alembic.ini"), "head")
except Exception as error:
    raise SystemExit(
        f"Explicit migration failed ({type(error).__name__}); values suppressed."
    ) from None
