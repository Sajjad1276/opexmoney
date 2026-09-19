"""Expand trader username length for the onboarding contract.

Revision ID: 0004_onboarding_username_length
Revises: 0003_living_economy_protocol
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_onboarding_username_length"
down_revision = "0003_living_economy_protocol"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "username",
        existing_type=sa.String(length=15),
        type_=sa.String(length=20),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "username",
        existing_type=sa.String(length=20),
        type_=sa.String(length=15),
        existing_nullable=False,
    )
