from scripts import repair_alembic_state


def test_repair_chain_includes_latest_admin_revisions() -> None:
    assert repair_alembic_state.REVISION_CHAIN[-3:] == [
        "0020_rate_history_factors",
        "0021_admin_panel_fields",
        "0022_admin_price_alert_active",
    ]


def test_rate_history_factor_columns_are_part_of_revision_detection() -> None:
    source = open("scripts/repair_alembic_state.py", encoding="utf-8").read()
    for column in (
        "dominant_cause",
        "pressure_signal",
        "foreign_signal",
        "activity_score",
        "trade_score",
        "growth_score",
    ):
        assert column in source


def test_latest_repair_revision_detection_sources_are_present() -> None:
    source = open("scripts/repair_alembic_state.py", encoding="utf-8").read()
    for column in (
        "target_type",
        "duration_days",
        "reward_xp",
        "ban_reason",
        "is_active",
        "triggered_value",
    ):
        assert column in source
