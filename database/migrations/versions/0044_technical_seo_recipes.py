"""Persist image evidence and sealed technical recipe revisions.

Owner: evidence-backed candidate recipes. Forward-only; see database/README.md.
"""

from pathlib import Path

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; use the reviewed recovery plan.")
