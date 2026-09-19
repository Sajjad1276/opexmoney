from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    ONBOARDING_NAME = State()
    SET_USERNAME_PLAYER = State()
    SELECT_NATION = State()
