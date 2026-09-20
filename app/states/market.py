from aiogram.fsm.state import State, StatesGroup


class MarketStates(StatesGroup):
    WAITING_BUY_AMOUNT = State()
    WAITING_SELL_AMOUNT = State()
    WAITING_ALERT_CURRENCY = State()
    WAITING_ALERT_PRICE = State()
    WAITING_CHART_CURRENCY = State()
