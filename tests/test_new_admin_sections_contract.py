from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "admin" / "static" / "index.html"
PANEL = ROOT / "admin" / "routers" / "panel_sections.py"
ADMIN_MAIN = ROOT / "admin" / "main.py"
MODELS = ROOT / "app" / "database" / "models.py"
MIGRATION = ROOT / "alembic" / "versions" / "0021_admin_panel_fields.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_exact_ten_new_sections_are_present() -> None:
    html = read(STATIC)
    expected = {
        "section-academy",
        "section-missions",
        "section-founding",
        "section-ai",
        "section-leaderboard",
        "section-profiles",
        "section-onboarding",
        "section-scheduler",
        "section-support",
        "section-treasury",
    }
    sections = set(re.findall(r'<section id="(section-[^"]+)"', html))
    nav = set(re.findall(r'data-s="(section-[^"]+)"', html))
    assert sections == expected
    assert nav == expected


def test_exact_panel_apis_are_wired() -> None:
    panel = read(PANEL)
    html = read(STATIC)
    required = [
        '@router.get("/api/academy/stats")',
        '@router.get("/api/academy/lessons")',
        '@router.post("/api/academy/lessons")',
        '@router.patch("/api/academy/lessons/{lesson_id}")',
        '@router.patch("/api/academy/lessons/{lesson_id}/toggle")',
        '@router.get("/api/academy/progress")',
        '@router.get("/api/missions/stats")',
        '@router.get("/api/missions")',
        '@router.post("/api/missions")',
        '@router.patch("/api/missions/{mission_id}")',
        '@router.patch("/api/missions/{mission_id}/toggle")',
        '@router.get("/api/missions/{mission_id}/stats")',
        '@router.get("/api/founding/stats")',
        '@router.get("/api/founding/drafts")',
        '@router.get("/api/founding/drafts/{draft_id}")',
        '@router.post("/api/founding/drafts/{draft_id}/cancel")',
        '@router.post("/api/founding/drafts/{draft_id}/finalize")',
        '@router.get("/api/ai/stats")',
        '@router.get("/api/ai/gemini-health")',
        '@router.get("/api/ai/players")',
        '@router.patch("/api/ai/players/{user_id}/strategy")',
        '@router.patch("/api/ai/players/{user_id}/tier")',
        '@router.patch("/api/ai/players/{user_id}/toggle")',
        '@router.get("/api/ai/scheduler-status")',
        '@router.post("/api/ai/scheduler/pause")',
        '@router.post("/api/ai/scheduler/resume")',
        '@router.post("/api/ai/scheduler/run-now")',
        '@router.get("/api/leaderboard/nations")',
        '@router.get("/api/leaderboard/wealth")',
        '@router.get("/api/leaderboard/traders")',
        '@router.get("/api/profiles/search")',
        '@router.get("/api/profiles/{user_id}")',
        '@router.post("/api/profiles/{user_id}/ban")',
        '@router.post("/api/profiles/{user_id}/unban")',
        '@router.patch("/api/profiles/{user_id}/home-nation")',
        '@router.get("/api/onboarding/funnel")',
        '@router.get("/api/onboarding/active-accounts")',
        '@router.get("/api/scheduler/jobs")',
        '@router.post("/api/scheduler/jobs/{job_id}/{action}")',
        '@router.get("/api/support/stats")',
        '@router.get("/api/support/telemetry/{user_id}")',
        '@router.get("/api/support/recent-errors")',
        '@router.get("/api/treasury/overview")',
        '@router.post("/api/treasury/adjust")',
        '@router.get("/api/treasury/logs")',
    ]
    for endpoint in required:
        assert endpoint in panel, endpoint

    for path in [
        "/api/academy/stats",
        "/api/missions/stats",
        "/api/founding/stats",
        "/api/ai/stats",
        "/api/leaderboard/nations",
        "/api/profiles/search",
        "/api/onboarding/funnel",
        "/api/scheduler/jobs",
        "/api/support/stats",
        "/api/treasury/overview",
    ]:
        assert path in html, path


def test_panel_router_registered_and_admin_mutations_audited() -> None:
    main = read(ADMIN_MAIN)
    panel = read(PANEL)
    assert "app.include_router(panel_sections.router)" in main
    for name in [
        "update_academy_lesson_v2",
        "create_mission_v2",
        "update_mission_v2",
        "founding_cancel",
        "founding_finalize",
        "update_ai_strategy",
        "update_ai_tier",
        "toggle_ai_player",
        "profile_ban",
        "profile_unban",
        "profile_home_nation",
        "treasury_adjust_v2",
    ]:
        m = re.search(rf"async def {name}\([\s\S]*?(?=\n@router\.|\Z)", panel)
        assert m is not None, name
        if name not in {"profile_ban", "profile_unban"}:
            assert "_audit(" in m.group(0), name


def test_panel_source_does_not_use_placeholder_api_values() -> None:
    html = read(STATIC)
    assert "TODO" not in html
    assert "PLACEHOLDER" not in html
    assert "setInterval" in html
    assert "window._intervals.push" in html


def test_admin_panel_model_and_migration_fields_exist() -> None:
    models = read(MODELS)
    migration = read(MIGRATION)
    assert "reward_xp" in models
    assert "target_type" in models
    assert "duration_days" in models
    assert "ban_reason" in models
    assert 'revision = "0021_admin_panel_fields"' in migration
    assert 'down_revision = "0020_rate_history_factors"' in migration
