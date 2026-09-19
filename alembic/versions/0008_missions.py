"""missions

Revision ID: 0008_missions
Revises: 0007_seed_starter_nations
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_missions"
down_revision = "0007_seed_starter_nations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "missions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("title_fa", sa.String(100), nullable=False),
        sa.Column("description_fa", sa.String(255), nullable=False),
        sa.Column("mission_type", sa.String(10), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column(
            "reward_xr",
            sa.Numeric(18, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reward_currency",
            sa.Numeric(18, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.UniqueConstraint("key", name="uq_missions_key"),
    )

    op.create_table(
        "user_mission_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mission_id",
            sa.Integer(),
            sa.ForeignKey("missions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "progress",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "claimed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("reset_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "user_id",
            "mission_id",
            name="uq_user_mission",
        ),
    )

    missions = sa.table(
        "missions",
        sa.column("key", sa.String(50)),
        sa.column("title_fa", sa.String(100)),
        sa.column("description_fa", sa.String(255)),
        sa.column("mission_type", sa.String(10)),
        sa.column("target_count", sa.Integer()),
        sa.column("reward_xr", sa.Numeric(18, 4)),
        sa.column("reward_currency", sa.Numeric(18, 4)),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        missions,
        [
            {
                "key": "DAILY_TRADE_1",
                "title_fa": "اولین معامله روز",
                "description_fa": "امروز یک معامله انجام بده",
                "mission_type": "daily",
                "target_count": 1,
                "reward_xr": 5,
                "reward_currency": 0,
                "is_active": True,
            },
            {
                "key": "DAILY_LOGIN",
                "title_fa": "ورود روزانه",
                "description_fa": "هر روز وارد ربات شو",
                "mission_type": "daily",
                "target_count": 1,
                "reward_xr": 2,
                "reward_currency": 0,
                "is_active": True,
            },
            {
                "key": "WEEKLY_BUY_5",
                "title_fa": "۵ خرید در هفته",
                "description_fa": "این هفته ۵ بار خرید انجام بده",
                "mission_type": "weekly",
                "target_count": 5,
                "reward_xr": 25,
                "reward_currency": 0,
                "is_active": True,
            },
            {
                "key": "WEEKLY_TRADE_VOLUME",
                "title_fa": "حجم معاملات هفتگی",
                "description_fa": "این هفته ۱۰۰۰ واحد معامله داشته باش",
                "mission_type": "weekly",
                "target_count": 1000,
                "reward_xr": 50,
                "reward_currency": 0,
                "is_active": True,
            },
            {
                "key": "FIRST_TRADE",
                "title_fa": "اولین معامله تاریخ",
                "description_fa": "اولین خرید یا فروشت رو انجام بده",
                "mission_type": "permanent",
                "target_count": 1,
                "reward_xr": 10,
                "reward_currency": 0,
                "is_active": True,
            },
            {
                "key": "JOIN_NATION",
                "title_fa": "عضو ملت شدی",
                "description_fa": "به یه ملت بپیوند",
                "mission_type": "permanent",
                "target_count": 1,
                "reward_xr": 20,
                "reward_currency": 0,
                "is_active": True,
            },
            {
                "key": "RICH_PLAYER",
                "title_fa": "بازیکن ثروتمند",
                "description_fa": "موجودی ΩXR بالای ۵۰۰۰ داشته باش",
                "mission_type": "permanent",
                "target_count": 5000,
                "reward_xr": 100,
                "reward_currency": 0,
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("user_mission_progress")
    op.drop_table("missions")
