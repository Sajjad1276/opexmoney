from aiogram.fsm.state import State, StatesGroup


class FounderStates(StatesGroup):
    WAITING_FOR_GROUP = State()
    SET_NATION_NAME = State()
    SET_CURRENCY_CODE = State()
    CONFIRM_CREATE = State()
