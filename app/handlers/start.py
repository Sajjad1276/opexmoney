from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from app.keyboards.inline import start_keyboard
from app.states.onboarding import OnboardingState

router = Router()

START_TEXT = '''<b>سلام.</b>

بازارهای جهانی OPEX هر روز میلیاردها واحد ارز جابه‌جا می‌کنند.

بعضی‌ها ثروت می‌سازند.
بعضی‌ها ملت می‌سازند.

<b>تو چی می‌خوای؟</b>'''

@router.message(CommandStart())
async def start(message: Message):
    await message.answer(START_TEXT, reply_markup=start_keyboard())

@router.callback_query(F.data == 'start_founder')
async def founder(call: CallbackQuery):
    await call.answer()
    await call.message.edit_text('این بخش به زودی فعال میشه.')

