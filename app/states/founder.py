from aiogram.fsm.state import State, StatesGroup


class FounderStates(StatesGroup):
    WAITING_GROUP_ADMIN = State()
    SET_NATION_NAME = State()
    SELECT_FLAG = State()
    CONFIRM = State()
