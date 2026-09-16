from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    SET_USERNAME_PLAYER = State()
