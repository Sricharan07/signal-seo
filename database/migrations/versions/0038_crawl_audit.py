"""Store evidence-bound technical audit reports for completed crawls.

Owner: crawl evidence. Forward-only; see database/README.md.
"""

from pathlib import Path

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; use the reviewed recovery plan.")
