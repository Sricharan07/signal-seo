"""Recovery-bound owner risk acceptance for an unprotected default branch."""

from pathlib import Path

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; use the reviewed recovery plan.")
