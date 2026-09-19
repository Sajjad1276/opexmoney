"""Seed the starter nations required by fresh-user onboarding.

Revision ID: 0007_seed_starter_nations
Revises: 0006_bigint_history_identities
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_seed_starter_nations"
down_revision = "0006_bigint_history_identities"
branch_labels = None
depends_on = None


STARTER_NATIONS = (
    ("پارِس", "PRS"),
    ("آریا", "ARY"),
    ("سپهر", "SPH"),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    has_any = bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM nations WHERE is_active = TRUE)")
    ).scalar()
    if has_any:
        return

    for name, currency in STARTER_NATIONS:
        bind.execute(
            sa.text(
                """
                INSERT INTO nations (
                    group_id,
                    name,
                    currency_code,
                    founder_user_id,
                    exchange_rate,
                    rate_prev,
                    rate_24h_open,
                    trade_volume_24h,
                    active_members_24h,
                    nation_rank,
                    last_rate_update,
                    member_count,
                    is_active,
                    join_policy,
                    personality,
                    invite_code,
                    treasury
                )
                VALUES (
                    NULL,
                    :name,
                    :currency,
                    NULL,
                    1.0000,
                    1.0000,
                    1.0000,
                    0,
                    0,
                    NULL,
                    CURRENT_TIMESTAMP,
                    0,
                    TRUE,
                    'OPEN',
                    'neutral',
                    :invite_code,
                    0.00
                )
                ON CONFLICT (currency_code) DO NOTHING
                """
            ),
            {
                "name": name,
                "currency": currency,
                "invite_code": f"OPX-{currency}-START",
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    currencies = tuple(currency for _, currency in STARTER_NATIONS)
    bind.execute(
        sa.text(
            "DELETE FROM nations "
            "WHERE founder_user_id IS NULL "
            "AND currency_code = ANY(:currencies)"
        ),
        {"currencies": list(currencies)},
    )
