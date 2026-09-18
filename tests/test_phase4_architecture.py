from pathlib import Path

from app.services.intent_router import STEP_DEFINITIONS


ROOT = Path(__file__).resolve().parents[1]
HANDLERS = [
    ROOT / "app/handlers/start.py",
    ROOT / "app/handlers/onboarding_fix.py",
    ROOT / "app/handlers/founder.py",
    ROOT / "app/handlers/nation.py",
    ROOT / "app/handlers/sections.py",
    ROOT / "app/handlers/market.py",
    ROOT / "app/handlers/governance.py",
    ROOT / "app/handlers/interaction.py",
]


def test_handlers_do_not_send_keyboards_directly():
    offenders = []
    for path in HANDLERS:
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            if "reply_markup" in line:
                offenders.append(f"{path.relative_to(ROOT)}:{number}:{line.strip()}")
    assert not offenders, "Direct keyboard transition found:\n" + "\n".join(offenders)
    print("ARCH|PASS|no handler-level reply_markup usage")


def test_keyboard_state_and_draft_models_are_migrated():
    migration = ROOT / "alembic/versions/0004_phase4_ux.py"
    source = migration.read_text(encoding="utf-8")
    assert "onboarding_drafts" in source
    assert "keyboard_states" in source
    print("ARCH|PASS|migration=0004_phase4_ux")


def test_all_defined_fsm_states_have_step_definitions():
    source_files = [
        ROOT / "app/states/onboarding.py",
        ROOT / "app/states/founder.py",
        ROOT / "app/states/market.py",
        ROOT / "app/states/governance.py",
    ]
    states = []
    for path in source_files:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("class ") and "States" in line:
                current_group = line.split("class ", 1)[1].split("(", 1)[0]
            if line.endswith("State()") and ":" in line:
                name = line.split(":", 1)[0].strip()
                if current_group:
                    states.append(f"{current_group}:{name}")
    assert set(states).issubset(STEP_DEFINITIONS)
    print(f"ARCH|PASS|step_definitions={len(STEP_DEFINITIONS)}")
