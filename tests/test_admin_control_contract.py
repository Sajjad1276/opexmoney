from __future__ import annotations

import re
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "admin" / "routers" / "control.py"
ADMIN_MAIN = ROOT / "admin" / "main.py"
BOT_MAIN = ROOT / "main.py"
STATIC = ROOT / "admin" / "static" / "index.html"


EXPECTED_SECTIONS = {
    "overview",
    "economy",
    "market",
    "players",
    "nations",
    "wars",
    "governance",
    "transactions",
    "actions",
    "logs",
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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_admin_control_router_is_registered() -> None:
    admin_main = _read(ADMIN_MAIN)
    control = _read(CONTROL)

    assert "from admin.routers import (" in admin_main
    assert "    control," in admin_main
    assert "app.include_router(control.router)" in admin_main
    assert 'router = APIRouter(prefix="/api/control"' in control


def test_admin_control_endpoints_cover_missing_systems() -> None:
    control = _read(CONTROL)
    required = [
        '@router.get("/academy/summary")',
        '@router.get("/academy/lessons")',
        '@router.post("/academy/lessons"',
        '@router.patch("/academy/lessons/{lesson_id}"',
        '@router.get("/academy/progress")',
        '@router.get("/missions")',
        '@router.post("/missions"',
        '@router.patch("/missions/{mission_id}")',
        '@router.get("/founder/summary")',
        '@router.get("/founder/drafts")',
        '@router.post("/founder/drafts/{draft_id}/cancel")',
        '@router.post("/founder/drafts/{draft_id}/finalize")',
        '@router.get("/ai/summary")',
        '@router.get("/ai/users")',
        '@router.patch("/ai/users/{user_id}")',
        '@router.get("/ai/gemini-health")',
        '@router.get("/ranking")',
        '@router.get("/profiles")',
        '@router.get("/onboarding/summary")',
        '@router.get("/behavior-snapshots")',
        '@router.get("/price-alerts")',
        '@router.get("/scheduler")',
        '@router.post("/scheduler/{job_id}/{command}")',
        '@router.get("/support/overview")',
        '@router.get("/support/users/{user_id}")',
        '@router.get("/treasury")',
        '@router.get("/treasury/{nation_id}/logs")',
        '@router.post("/treasury/{nation_id}/adjust")',
    ]
    for endpoint in required:
        assert endpoint in control, endpoint


def test_admin_control_mutations_are_audited() -> None:
    control = _read(CONTROL)

    mutation_blocks = [
        "update_academy_lesson",
        "create_mission",
        "update_mission",
        "update_ai_user",
        "cancel_founder_draft",
        "treasury_adjust",
    ]
    for function_name in mutation_blocks:
        match = re.search(
            rf"async def {function_name}\([\s\S]*?(?=\n@router\.|\Z)",
            control,
        )
        assert match is not None, function_name
        assert "_audit(" in match.group(0), function_name


def test_admin_static_contains_all_control_sections_and_initializers() -> None:
    html = _read(STATIC)

    sections = set(re.findall(r'<section id="([^"]+)" class="section', html))
    assert sections == EXPECTED_SECTIONS

    nav_sections = set(re.findall(r'<button data-s="([^"]+)"', html))
    assert nav_sections == EXPECTED_SECTIONS

    init_names = {
        "overview": "initSectionOverview",
        "economy": "initSectionEconomy",
        "market": "initSectionMarket",
        "players": "initSectionPlayers",
        "nations": "initSectionNations",
        "wars": "initSectionWars",
        "governance": "initSectionGovernance",
        "transactions": "initSectionTransactions",
        "actions": "initSectionActions",
        "logs": "initSectionLogs",
        "section-academy": "initSectionAcademyV2",
        "section-missions": "initSectionMissionsV2",
        "section-founding": "initSectionFoundingV2",
        "section-ai": "initSectionAIV2",
        "section-leaderboard": "initSectionLeaderboardV2",
        "section-profiles": "initSectionProfilesV2",
        "section-onboarding": "initSectionOnboardingV2",
        "section-scheduler": "initSectionSchedulerV2",
        "section-support": "initSectionSupportV2",
        "section-treasury": "initSectionTreasuryV2",
    }
    for section, function_name in init_names.items():
        assert f"{function_name}()" in html, section


def test_admin_static_has_control_api_wiring() -> None:
    html = _read(STATIC)
    required_paths = [
        "/api/control/academy/summary",
        "/api/control/academy/lessons",
        "/api/control/missions",
        "/api/control/founder/summary",
        "/api/control/ai/summary",
        "/api/control/ai/gemini-health",
        "/api/control/ranking",
        "/api/control/profiles",
        "/api/control/onboarding/summary",
        "/api/economy/behavior-snapshots",
        "/api/control/price-alerts",
        "/api/control/scheduler",
        "/api/control/support/overview",
        "/api/treasury/overview",
    ]
    for path in required_paths:
        assert path in html, path

    assert "api.patch" in html
    assert "api.post" in html
    assert 'allow_methods=["GET", "POST", "PATCH", "OPTIONS"]' in _read(ADMIN_MAIN)


def test_scheduler_admin_bridge_is_registered() -> None:
    bot_main = _read(BOT_MAIN)
    scheduler_bridge = ROOT / "app" / "schedulers" / "admin_control.py"
    bridge = _read(scheduler_bridge)

    assert "from app.schedulers.admin_control import" in bot_main
    assert "install_scheduler_monitor(scheduler, ranking_redis)" in bot_main
    assert "scheduler_control_loop(scheduler, ranking_redis)" in bot_main

    expected_jobs = {
        "rate_engine_15m",
        "nation_membership_reconciliation_15m",
        "nation_rank_hourly",
        "governance_cycle",
        "daily_market_reset",
        "nation_war_resolution_15m",
        "nation_join_request_expiration",
        "nation_weekly_ai_report",
        "price_alert_checker_5m",
        "ai_world_5m",
        "portfolio_live_update_10s",
    }
    for job_id in expected_jobs:
        assert f'"{job_id}"' in bridge

    assert '"pause"' in bridge
    assert '"resume"' in bridge
    assert '"run_now"' in bridge


def test_admin_control_and_scheduler_sources_parse_and_import_paths() -> None:
    control = _read(CONTROL)
    scheduler = _read(ROOT / "app" / "schedulers" / "admin_control.py")
    assert "from ai import companion" in control
    assert "from app.ai import companion" not in control
    ast.parse(control)
    ast.parse(scheduler)


def test_sensitive_control_calls_require_explicit_confirmation() -> None:
    control = _read(CONTROL)
    html = _read(STATIC)

    founder = control[control.index('@router.post("/founder/drafts/{draft_id}/finalize")'):]
    treasury = control[control.index('@router.post("/treasury/{nation_id}/adjust")'):]
    assert "confirm: bool = Query(default=False)" in founder
    assert "confirm: bool = Query(default=False)" in treasury
    assert "/api/control/founder/drafts/'+b.dataset.founderFinalize+'/finalize?confirm=true" in html
    assert "/api/treasury/adjust" in html


def test_admin_actions_have_frontend_payload_contracts() -> None:
    html = _read(STATIC)
    required = {
        "broadcast": ["af-message", "af-target"],
        "give-bonus": ["af-user", "af-amount", "af-note"],
        "ban-player": ["af-user", "af-note"],
        "unban-player": ["af-user"],
        "reset-rates": ["af-nation", "af-rate", "af-note", "af-reset"],
        "end-war": ["af-war", "af-result", "af-note"],
        "create-event": [
            "af-event-type",
            "af-scope",
            "af-title",
            "af-description",
            "af-effect",
            "af-magnitude",
            "af-duration",
            "af-event-nation",
            "af-currency",
        ],
        "send-mission-reward": ["af-user-ids", "af-mission-key", "af-override"],
    }
    for action, ids in required.items():
        assert action in html, action
        for field_id in ids:
            assert f'id="{field_id}"' in html, (action, field_id)

    assert "function actionPayload(id)" in html
    assert "actionEndpoint[id]+'?confirm=false'" in html
    assert "actionEndpoint[id]+'?confirm=true'" in html


def test_admin_action_endpoints_match_router_contracts() -> None:
    html = _read(STATIC)
    actions = _read(ROOT / "admin" / "routers" / "actions.py")
    mapping = {
        "broadcast": '"/broadcast"',
        "give-bonus": '"/give-bonus"',
        "ban-player": '"/ban-player"',
        "unban-player": '"/unban-player"',
        "reset-rates": '"/reset-rates"',
        "end-war": '"/end-war"',
        "create-event": '"/create-event"',
        "send-mission-reward": '"/send-mission-reward"',
    }
    for action, route in mapping.items():
        assert action in html, action
        assert f"@router.post({route}" in actions, route
