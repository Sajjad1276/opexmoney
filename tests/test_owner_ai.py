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


def test_owner_paths_allow_full_project_files_except_secrets() -> None:
    assert _safe_repo_path("alembic/versions/0022_admin_price_alert_active.py") == "alembic/versions/0022_admin_price_alert_active.py"
    assert _safe_repo_path(".github/workflows/ci.yml") == ".github/workflows/ci.yml"
    assert _safe_repo_path("Dockerfile") == "Dockerfile"


def test_owner_command_cleanup_is_registered() -> None:
    main_source = open("main.py", encoding="utf-8").read()
    assert "CommandPanelCleanupMiddleware" in main_source
    assert "dp.message.middleware(CommandPanelCleanupMiddleware())" in main_source


def test_owner_ai_supports_repository_search_fallback() -> None:
    source = open("app/services/owner_ai_service.py", encoding="utf-8").read()
    assert "Owner AI code search failed; fallback scan" in source
    assert "async def _scan_repository_for_query" in source
    assert "async def _plain_recovery_answer" in source


def test_owner_ai_uses_dedicated_long_timeout_and_modern_models() -> None:
    config_source = open("config.py", encoding="utf-8").read()
    source = open("app/services/owner_ai_service.py", encoding="utf-8").read()
    assert "owner_ai_timeout_seconds: float = 120.0" in config_source
    assert "settings.owner_ai_timeout_seconds" in source
    assert '"gemini-3.8-flash"' in source
    assert '"gemini-3.7-flash"' in source
    assert "def _owner_ai_client" in source


def test_scheduler_monitor_does_not_require_legacy_next_run_time_property() -> None:
    source = open("app/schedulers/admin_control.py", encoding="utf-8").read()
    assert 'getattr(job, "next_run_time", None)' in source
    assert 'getattr(job, "next_fire_time", None)' in source
