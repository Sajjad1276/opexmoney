from aiogram.fsm.state import State, StatesGroup


class MarketStates(StatesGroup):
    WAITING_BUY_AMOUNT = State()
    WAITING_SELL_AMOUNT = State()
    WAITING_ALERT_CURRENCY = State()\n    WAITING_ALERT_PRICE = State()\n    WAITING_CHART_CURRENCY = State()
