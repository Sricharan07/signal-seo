"""Owner-granted, recovery-bound standing authorization and aggregate budgets."""

from pathlib import Path

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; use the reviewed recovery plan.")
