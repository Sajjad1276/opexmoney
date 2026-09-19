"""academy learning system

Revision ID: 0010_academy
Revises: 0009_treasury
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_academy"
down_revision = "0009_treasury"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lessons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("module_id", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("title_fa", sa.String(100), nullable=False),
        sa.Column("content_fa", sa.String(4000), nullable=False),
        sa.Column(
            "quiz_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "xp_reward",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("10"),
        ),
        sa.Column(
            "xr_reward",
            sa.Numeric(10, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.UniqueConstraint(
            "module_id",
            "order",
            name="uq_lesson_module_order",
        ),
    )

    op.create_table(
        "user_lesson_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lesson_id",
            sa.Integer(),
            sa.ForeignKey("lessons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'locked'"),
        ),
        sa.Column("quiz_score", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "ai_questions_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "lesson_id",
            name="uq_user_lesson",
        ),
    )

    op.create_table(
        "user_xp",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "total_xp",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "level",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'beginner'"),
        ),
        sa.Column(
            "last_updated",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    op.create_index(
        "ix_user_lesson_progress_user_id",
        "user_lesson_progress",
        ["user_id"],
    )
    op.create_index(
        "ix_lessons_module_id",
        "lessons",
        ["module_id", "order"],
    )

    op.execute(
        """
        INSERT INTO user_xp (user_id, total_xp, level, last_updated)
        SELECT user_id, 0, 'beginner', now() FROM users
        ON CONFLICT (user_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("user_xp")
    op.drop_table("user_lesson_progress")
    op.drop_table("lessons")
