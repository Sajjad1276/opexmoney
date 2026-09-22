from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.database.models import User
from app.database.session import async_session
from app.keyboards.inline import support_panel_keyboard, support_result_keyboard
from app.services.support.support_service import support_service
from app.states.support import SupportStates
from app.utils.ui import close_inline_panel, remember_inline_panel, send_submenu_panel

router = Router(name="support")

INTRO_TEXT = (
    "🛟 <b>پشتیبانی هوشمند</b>\n"
    "\n"
    "مشکلت رو به زبان خودت بنویس.\n"
    "سیستم وضعیت حساب، مسیر آخر و خطاهای اخیر رو بررسی می‌کنه.\n\n"
    "<i>مثال: بازار برای من باز نمی‌شه یا بعد از زدن دکمه خطا می‌ده.</i>"
)

PROMPT_TEXT = (
    "🛟 <b>مرکز پشتیبانی OPEX</b>\n\n"
    "مشکل را همان‌طور که تجربه‌اش کردی بنویس.\n"
    "اوپکس نشانه‌ها را بررسی می‌کند، ریشه مشکل را پیدا می‌کند و تا جای ممکن مسیر اصلاح را پیشنهاد می‌دهد.\n\n"
    "<i>مثال: بازار باز می‌شود ولی فهرست ارزها نمایش داده نمی‌شود.</i>"
)