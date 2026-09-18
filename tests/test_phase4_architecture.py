from pathlib import Path
import re

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
        current_group = None
        for line in text.splitlines():
            class_match = re.match(r"^class\s+(\w+States)\(", line)
            if class_match:
                current_group = class_match.group(1)
            state_match = re.match(r"^\s*(\w+)\s*=\s*State\(\)\s*$", line)
            if current_group and state_match:
                states.append(f"{current_group}:{state_match.group(1)}")
    assert set(states).issubset(STEP_DEFINITIONS)
    print(f"ARCH|PASS|step_definitions={len(STEP_DEFINITIONS)}")
