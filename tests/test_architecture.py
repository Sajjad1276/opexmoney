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
    assert ".commit(" not in market
    assert ".commit(" not in economy


def test_real_alembic_structure_exists():
    assert (ROOT / "alembic.ini").exists()
    assert (ROOT / "alembic/env.py").exists()
    assert (ROOT / "alembic/versions/0001_initial_schema.py").exists()
    assert (ROOT / "alembic/versions/0002_founder_constraints.py").exists()
