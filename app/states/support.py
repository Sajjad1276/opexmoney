from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    WAITING_REPORT = State()
