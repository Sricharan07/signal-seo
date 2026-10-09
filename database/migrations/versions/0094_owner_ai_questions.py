"""Owner question versions and accepted visibility recipe handoff."""

from pathlib import Path

from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; use the reviewed recovery plan.")
