from scripts import repair_alembic_state


def test_repair_chain_includes_rate_history_factors_revision() -> None:
    assert repair_alembic_state.REVISION_CHAIN[-1] == "0020_rate_history_factors"


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
