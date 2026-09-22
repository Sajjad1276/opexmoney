from types import SimpleNamespace
import pytest

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_founder_router_precedes_generic_start_router():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    founder_positions = [m.start() for m in re.finditer(r"dp\.include_router\(founder_router\)", source)]
    assert len(founder_positions) == 1
    founder_pos = founder_positions[0]
    start_pos = source.index("dp.include_router(start_router)")
    assert founder_pos < start_pos


def test_founder_group_deeplink_has_a_real_handler():
    source = (ROOT / "app/handlers/founder_flow.py").read_text(encoding="utf-8")
    assert 'F.text.regexp(r"^/start(?:@[^ ]+)?\\s+founder_([A-Za-z0-9_-]+)$")' in source
    assert "async def group_founder_start(" in source


def test_founder_finalization_uses_canonical_founder_service():
    source = (ROOT / "app/services/nation/founder_service.py").read_text(encoding="utf-8")
    assert "create_nation as create_nation_core" not in source
    assert "nation = await create_nation(" in source
    assert "group_chat_id=group_id" in source
    assert "flag_emoji=flag_emoji" in source


def test_founder_text_state_handlers_are_private_only():
    source = (ROOT / "app/handlers/founder_flow.py").read_text(encoding="utf-8")
    for state_name in (
        "SET_USERNAME_FOUNDER",
        "SET_CURRENCY_CODE",
        "SET_NATION_NAME",
        "WAITING_GROUP_ADMIN",
        "SELECT_FLAG",
        "CONFIRM",
    ):
        assert (
            f"FounderStates.{state_name},\n"
            '    F.chat.type == "private",\n'
            "    F.text"
        ) in source

def test_founder_button_callbacks_have_handlers():
    source = (ROOT / "app/handlers/founder_flow.py").read_text(encoding="utf-8")
    assert 'F.data.in_({"start_founder", "found_nation"})' in source
    assert "async def start_founder(" in source

    callbacks = {
        "founder_has_group": "founder_has_group",
        "founder_no_group": "founder_no_group",
        "founder_check_group": "check_founder_group",
        "founder_announce": "founder_announce_ack",
        "founder_recode": "founder_recode",
        "founder_edit_name": "founder_edit_name",
        "founder_edit_flag": "founder_edit_flag",
        "founder_edit_group": "founder_edit_group",
        "confirm_found": "confirm_founder",
        "cancel_founder": "cancel_founder",
    }
    for callback, handler in callbacks.items():
        assert f'F.data == "{callback}"' in source
        assert f"async def {handler}(" in source
    assert 'F.data.startswith("founder_flag:")' in source
    assert "async def select_founder_flag(" in source



class _FounderState:
    def __init__(self):
        self.cleared = False

    async def clear(self):
        self.cleared = True


class _FounderMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class _FounderCall:
    def __init__(self):
        self.from_user = SimpleNamespace(id=991001, first_name="Founder")
        self.message = _FounderMessage()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
async def test_founder_entry_callback_executes():
    from app.handlers.founder_flow import start_founder

    call = _FounderCall()
    state = _FounderState()

    await start_founder(call, state, bot=SimpleNamespace())

    assert state.cleared is True
    assert call.answers
    assert len(call.message.edits) == 1

    text, kwargs = call.message.edits[0]
    assert "ساخت ملت جدید" in text
    markup = kwargs["reply_markup"]
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert "founder_has_group" in callbacks
    assert "founder_no_group" in callbacks
