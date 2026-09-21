from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_startup_does_not_mutate_schema():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "create_all(" not in source
    assert "ALTER TABLE" not in source
    assert "information_schema" not in source


def test_market_and_economic_services_do_not_commit():
    market = (ROOT / "app/handlers/market.py").read_text(encoding="utf-8")
    economy = (ROOT / "app/services/economic_engine.py").read_text(encoding="utf-8")
    governance = (ROOT / "app/services/governance_service.py").read_text(encoding="utf-8")
    assert ".commit(" not in market
    assert ".commit(" not in economy
    assert ".commit(" not in governance


def test_real_alembic_structure_exists():
    assert (ROOT / "alembic.ini").exists()
    assert (ROOT / "alembic/env.py").exists()
    assert (ROOT / "alembic/versions/0001_initial_schema.py").exists()
    assert (ROOT / "alembic/versions/0002_founder_constraints.py").exists()
    assert (ROOT / "alembic/versions/0003_living_economy_protocol.py").exists()


def test_startup_does_not_seed_ai_world() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "await ensure_ai_population()" not in source


def test_onboarding_does_not_force_nation_selection() -> None:
    source = (ROOT / "app/handlers/onboarding_fix.py").read_text(encoding="utf-8")
    start = source.index("async def accept_valid_name")
    end = source.index("\n\n\n@router.callback_query", start)
    handler = source[start:end]
    assert "show_nation_selection" not in handler
