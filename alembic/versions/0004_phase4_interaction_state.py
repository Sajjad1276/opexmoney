"""Add Phase 4 persistent wizard and keyboard state.

Revision ID: 0004_phase4_interaction_state
Revises: 0003_living_economy_protocol
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_phase4_interaction_state"
down_revision = "0003_living_economy_protocol"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onboarding_drafts",
        sa.Column("player_id", sa.BigInteger(), primary_key=True),
        sa.Column("step_key", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["users.user_id"],
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "keyboard_states",
        sa.Column("player_id", sa.BigInteger(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False, server_default=sa.text("'none'")),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index(
        "ix_keyboard_states_chat_id",
        "keyboard_states",
        ["chat_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_keyboard_states_chat_id", table_name="keyboard_states")
    op.drop_table("keyboard_states")
    op.drop_table("onboarding_drafts")
