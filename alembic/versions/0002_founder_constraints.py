"""Add founder infrastructure and uniqueness constraints.

Revision ID: 0002_founder_constraints
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_founder_constraints"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("bot_groups",
        sa.Column("group_id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("uq_nations_currency_code","nations",["currency_code"],unique=True)
    op.create_index("uq_nations_active_group","nations",["group_id"],unique=True,postgresql_where=sa.text("is_active = TRUE"))


def downgrade() -> None:
    op.drop_index("uq_nations_active_group", table_name="nations")
    op.drop_index("uq_nations_currency_code", table_name="nations")
    op.drop_table("bot_groups")
