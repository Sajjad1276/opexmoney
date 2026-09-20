"""market query indexes

Revision ID: 0018_market_query_indexes
Revises: 0017_telegram_membership
"""

from alembic import op


revision = "0018_market_query_indexes"
down_revision = "0017_telegram_membership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_transactions_nation_created_type",
        "transactions",
        ["nation_id", "created_at", "transaction_type"],
    )
    op.create_index(
        "ix_trade_previews_lookup",
        "trade_previews",
        ["user_id", "nation_id", "side", "spend", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trade_previews_lookup",
        table_name="trade_previews",
    )
    op.drop_index(
        "ix_transactions_nation_created_type",
        table_name="transactions",
    )
