from aiogram.fsm.state import State, StatesGroup


class FounderStates(StatesGroup):
    WAITING_GROUP_LINK = State()
    SET_NATION_NAME = State()
    SET_CURRENCY_CODE = State()
    CONFIRM = State()
