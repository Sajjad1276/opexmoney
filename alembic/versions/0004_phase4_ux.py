"""Phase 4 core UX persistence.

Revision ID: 0004_phase4_ux
Revises: 0003_living_economy_protocol
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_phase4_ux"
down_revision = "0003_living_economy_protocol"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onboarding_drafts",
        sa.Column(
            "player_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("step_key", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index(
        "ix_onboarding_drafts_updated_at",
        "onboarding_drafts",
        ["updated_at"],
    )

    op.create_table(
        "keyboard_states",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default=sa.text("'none'")),
        sa.Column("name", sa.String(100), nullable=False, server_default=sa.text("''")),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )


def downgrade() -> None:
    op.drop_table("keyboard_states")
    op.drop_index(
        "ix_onboarding_drafts_updated_at",
        table_name="onboarding_drafts",
    )
    op.drop_table("onboarding_drafts")
