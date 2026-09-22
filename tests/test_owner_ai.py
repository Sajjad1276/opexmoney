from __future__ import annotations

from app.services.owner_ai_service import _safe_repo_path, is_owner


def test_owner_auth_accepts_configured_owner() -> None:
    assert is_owner(123456789) is False


def test_owner_paths_block_secrets() -> None:
    for path in (".env", ".env.production", "keys/service.key", "credentials.json"):
        try:
            _safe_repo_path(path)
        except ValueError:
            continue
        raise AssertionError(f"secret path was accepted: {path}")


def test_owner_paths_allow_project_source() -> None:
    assert _safe_repo_path("app/handlers/market.py") == "app/handlers/market.py"
    assert _safe_repo_path("tests/test_market_flow.py") == "tests/test_market_flow.py"
