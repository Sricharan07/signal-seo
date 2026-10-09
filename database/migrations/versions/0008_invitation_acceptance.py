"""Verified single-use invitation acceptance and authority provisioning.

Owner: identity and account authority. Forward-only; see database/README.md.
"""

from pathlib import Path

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; use the reviewed recovery plan.")
