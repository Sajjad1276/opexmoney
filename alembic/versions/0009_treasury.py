"""nation treasury

Revision ID: 0009_treasury
Revises: 0008_missions
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_treasury"
down_revision = "0008_missions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nation_treasury",
        sa.Column("nation_id", sa.Integer(), nullable=False),
        sa.Column(
            "balance_xr",
            sa.Numeric(18, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "balance_local",
            sa.Numeric(18, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_deposit_at", sa.DateTime(), nullable=True),
        sa.Column(
            "total_deposited",
            sa.Numeric(18, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.ForeignKeyConstraint(
            ["nation_id"],
            ["nations.nation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("nation_id"),
    )

    op.create_table(
        "treasury_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nation_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("amount_xr", sa.Numeric(18, 4), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["nation_id"],
            ["nations.nation_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.user_id"],
            ondelete="SET NULL",
        ),
    )

    op.create_index(
        "ix_treasury_logs_nation_created",
        "treasury_logs",
        ["nation_id", sa.text("created_at DESC")],
    )

    op.execute(
        """
        INSERT INTO nation_treasury (
            nation_id,
            balance_xr,
            balance_local,
            total_deposited
        )
        SELECT nation_id, 0, 0, 0
        FROM nations
        ON CONFLICT (nation_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("treasury_logs")
    op.drop_table("nation_treasury")
