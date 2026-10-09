"""Exact dashboard editorial authority for journaled article pull requests."""

from pathlib import Path

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())
    op.get_bind().exec_driver_sql(
        Path(__file__).with_name("0071_article_observation.sql").read_text()
    )
    op.get_bind().exec_driver_sql(
        Path(__file__).with_name("0071_article_measurement.sql").read_text()
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; use reviewed corrective migrations.")
