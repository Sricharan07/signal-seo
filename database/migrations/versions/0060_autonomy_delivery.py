"""Distinct reviewed autonomy eligibility and exact standing dispatch authority."""

from pathlib import Path

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())
    op.get_bind().exec_driver_sql(
        Path(__file__).with_name("0060_weekly_delivery_ports.sql").read_text()
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; use the reviewed recovery plan.")
