from __future__ import annotations

from aiogram.types import CallbackQuery

from app.handlers import nation_management_flow as _flow

nation_management_router = _flow.nation_management_router

# Compatibility exports used by tests and other handlers.
_welcome_member = _flow._welcome_member
_notify_founder = _flow._notify_founder
publish_nation_event_analysis = _flow.publish_nation_event_analysis
nation_admin_panel = _flow.nation_admin_panel
expire_join_requests = _flow.expire_join_requests
send_weekly_nation_reports = _flow.send_weekly_nation_reports
log_nation_event = _flow.log_nation_event
get_nation_log = _flow.get_nation_log

async def _join_user(*args, **kwargs):
    _flow._welcome_member = _welcome_member
    _flow._notify_founder = _notify_founder
    _flow.publish_nation_event_analysis = publish_nation_event_analysis
    return await _flow._join_user(*args, **kwargs)

async def _set_member_role(*args, **kwargs):
    return await _flow._set_member_role(*args, **kwargs)

async def kick_member(call: CallbackQuery, bot):
    _flow._notify_founder = _notify_founder
    _flow.publish_nation_event_analysis = publish_nation_event_analysis
    return await _flow.kick_member(call, bot)

__all__ = [
    "nation_management_router",
    "_join_user",
    "_set_member_role",
    "kick_member",
    "_welcome_member",
    "_notify_founder",
    "publish_nation_event_analysis",
    "nation_admin_panel",
    "expire_join_requests",
    "send_weekly_nation_reports",
    "log_nation_event",
    "get_nation_log",
]
