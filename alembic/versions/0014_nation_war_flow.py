"""add nation war end time

Revision ID: 0014_nation_war_flow
Revises: 0013_nation_flag
"""

from datetime import timedelta

from alembic import op
import sqlalchemy as sa


revision = "0014_nation_war_flow"
down_revision = "0013_nation_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nation_wars",
        sa.Column("ends_at", sa.DateTime(), nullable=True),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, declared_at FROM nation_wars "
            "WHERE ends_at IS NULL"
        )
    ).fetchall()

    for war_id, declared_at in rows:
        conn.execute(
            sa.text(
                "UPDATE nation_wars "
                "SET ends_at = :ends_at "
                "WHERE id = :war_id"
            ),
            {
                "war_id": war_id,
                "ends_at": declared_at + timedelta(hours=48),
            },
        )

    op.alter_column(
        "nation_wars",
        "ends_at",
        existing_type=sa.DateTime(),
        nullable=False,
    )
    op.create_index(
        "ix_nation_wars_status_ends_at",
        "nation_wars",
        ["status", "ends_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_nation_wars_status_ends_at", table_name="nation_wars")
    op.drop_column("nation_wars", "ends_at")
