from aiogram.fsm.state import State, StatesGroup


class GovernanceStates(StatesGroup):
    WAITING_VALUE = State()
    CONFIRM_PROPOSAL = State()
