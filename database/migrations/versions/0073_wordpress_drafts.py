"""Owner-approved Core REST draft creation with quarantined ambiguous outcomes."""

from pathlib import Path

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; use the reviewed recovery plan.")
