"""enforce human versus AI nation boundary

Revision ID: 0019_ai_human_boundary
Revises: 0018_economy_event_outbox
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_ai_human_boundary"
down_revision = "0018_economy_event_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE nations
            SET is_ai = TRUE
            WHERE is_ai = FALSE
              AND group_id IS NULL
              AND founder_user_id IS NULL
            """
        )
    )
    op.create_check_constraint(
        "ck_nations_ai_group_boundary",
        "nations",
        "(is_ai = TRUE AND group_id IS NULL) OR (is_ai = FALSE AND group_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_nations_ai_group_boundary",
        "nations",
        type_="check",
    )
