"""add persistent AI world markers

Revision ID: 0016_ai_world
Revises: 0015_nation_founding_drafts
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_ai_world"
down_revision = "0015_nation_founding_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nations",
        sa.Column(
            "is_ai",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_ai",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "ai_strategy",
            sa.String(length=20),
            nullable=False,
            server_default="balanced",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "ai_strategy")
    op.drop_column("users", "is_ai")
    op.drop_column("nations", "is_ai")
