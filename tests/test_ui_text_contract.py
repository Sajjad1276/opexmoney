from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


USER_FACING_PATHS = (
    ROOT / "app" / "handlers",
    ROOT / "app" / "keyboards",
    ROOT / "app" / "utils" / "formatting.py",
)


def _python_files():
    for path in USER_FACING_PATHS:
        if path.is_file():
            yield path
        else:
            yield from path.rglob("*.py")


def test_user_facing_text_contains_no_blockquote():
    offenders = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        if "<blockquote" in source.lower():
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"blockquote markup remains in user-facing files: {offenders}"


def test_treasury_back_supports_all_return_targets_without_missing_nation_import():
    source = (
        ROOT / "app" / "handlers" / "treasury.py"
    ).read_text(encoding="utf-8")

    assert "from app.database.models import Nation, NationMember, NationMemberRole, User" in source
    assert 'F.data.regexp(r"^treasury:back:\\d+:(?:dashboard|nations|management)$")' in source
    assert 'if return_target == "dashboard":' in source
    assert 'if return_target == "nations":' in source
    assert 'if return_target == "management":' in source
    assert "await callback.answer()" in source


def test_first_start_game_callback_uses_canonical_registration_flow():
    source = (ROOT / "app" / "handlers" / "start_flow.py").read_text(encoding="utf-8")
    assert "from app.handlers.onboarding import _begin_registration" in source
    assert '@router.callback_query(F.data == "start_game")' in source
    callback_start = source.index('@router.callback_query(F.data == "start_game")')
    callback_end = source.index("async def _show_help", callback_start)
    callback_block = source[callback_start:callback_end]
    assert "await _begin_registration(call.message, state, replace_inline=True)" in callback_block


def test_start_help_covers_current_core_game_systems():
    source = (ROOT / "app" / "handlers" / "start_flow.py").read_text(encoding="utf-8")
    required_sections = (
        "شروع بازی",
        "بازار",
        "ملت",
        "جنگ",
        "مأموریت‌های روزانه و هفتگی",
        "آکادمی",
        "رتبه‌بندی",
    )
    for section in required_sections:
        assert section in source, f"help text is missing section: {section}"
    assert "نرخ‌ها هر ۱۵ دقیقه" in source
    assert "۱۰٪ خزانه ملت بازنده" in source
