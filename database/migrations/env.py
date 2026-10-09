"""Explicit deployment migrations; never invoked by a request or application startup."""

import os

import psycopg
from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

if context.is_offline_mode():
    raise RuntimeError("Offline application is unsupported; use a reviewed live migration job.")

dsn = os.environ["SIGNAL_MIGRATION_DSN"]
engine = create_engine(
    "postgresql+psycopg://", creator=lambda: psycopg.connect(dsn), poolclass=NullPool
)
with engine.connect() as connection, connection.begin():
    identity = connection.execute(
        text(
            "SELECT current_user, pg_is_in_recovery(), rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        )
    ).one()
    if identity != ("signal_migrator", False, False, False):
        raise RuntimeError(
            "Migrations require the dedicated non-superuser migrator on the primary."
        )
    connection.execute(text("SET LOCAL lock_timeout = '3s'"))
    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(732194601)"))
    context.configure(connection=connection, version_table_schema="control", target_metadata=None)
    context.run_migrations()
engine.dispose()
