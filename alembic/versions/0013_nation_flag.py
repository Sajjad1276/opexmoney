"""add cosmetic nation flag

Revision ID: 0013_nation_flag
Revises: 0012_academy_seed

Adds an optional cosmetic flag emoji to nations. This field has no game-logic
semantics and is used only for Telegram UI presentation.
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_nation_flag"
down_revision = "0012_academy_seed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nations",
        sa.Column(
            "flag_emoji",
            sa.String(length=10),
            nullable=True,
            server_default="🏴",
        ),
    )
    op.execute("UPDATE nations SET flag_emoji = '🏴' WHERE flag_emoji IS NULL")


def downgrade() -> None:
    op.drop_column("nations", "flag_emoji")
