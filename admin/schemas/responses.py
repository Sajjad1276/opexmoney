from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TransactionItem(ResponseModel):
    id: int
    transaction_type: str
    spend_xr: float
    amount: float
    fee_xr: float
    rate: float
    nation_name: str
    nation_flag: str | None
    created_at: str


class RateHistoryPoint(ResponseModel):
    rate: float
    volume: float
    calculated_at: str
    dominant_cause: str | None
    pressure_signal: float | None


class NationMemberItem(ResponseModel):
    user_id: int
    username: str
    role: str
    balance: float
    joined_at: str


class NationTreasuryDetail(ResponseModel):
    balance_xr: float
    balance_local: float
    total_deposited: float
    last_deposit_at: str | None


class NationLogItem(ResponseModel):
    action_type: str
    actor_username: str | None
    created_at: str


class WarListItem(ResponseModel):
    war_id: int
    nation_name: str
    nation_flag: str | None
    opponent_name: str
    opponent_flag: str | None
    status: str
    declared_at: str
    ends_at: str
    ended_at: str | None
    nation_member_count: int
    opponent_member_count: int


class OverviewStats(ResponseModel):
    total_players: int
    active_players_24h: int
    total_nations: int
    active_wars: int
    transactions_today: int
    transaction_volume_today: float
    total_market_volume: float
    pending_proposals: int
    new_players_7d: int
    gini_coefficient: float
    top10_wealth_share: float


class EconomyHealth(ResponseModel):
    inflation_rate: float
    market_confidence: float
    total_liquidity: float
    avg_volatility: float
    wealth_concentration: float
    market_stability: float


class PlayerListItem(ResponseModel):
    user_id: int
    username: str
    balance: float
    xr_balance: float
    role: str
    ai_tier: str
    home_nation_name: str | None
    home_nation_flag: str | None
    created_at: str
    is_ai: bool
    total_transactions: int
    last_activity_at: str | None


class PlayerDetail(PlayerListItem):
    xp_total: int
    xp_level: str
    active_nation_role: str | None
    last_10_transactions: list[TransactionItem]
    mission_completed_count: int
    ai_usage_today: dict
    is_banned: bool


class NationListItem(ResponseModel):
    nation_id: int
    name: str
    flag_emoji: str | None
    currency_code: str
    exchange_rate: float
    rate_change_24h: float
    member_count: int
    treasury: float
    trade_volume_24h: float
    nation_rank: int | None
    is_active: bool
    join_policy: str
    is_ai: bool
    created_at: str
    active_members_24h: int
    war_status: str


class NationDetail(NationListItem):
    rate_history: list[RateHistoryPoint]
    top_members: list[NationMemberItem]
    treasury_detail: NationTreasuryDetail | None
    active_wars: list[WarListItem]
    recent_logs: list[NationLogItem]


class MarketStateItem(ResponseModel):
    currency_code: str
    nation_name: str
    nation_flag: str | None
    buy_pressure: float
    sell_pressure: float
    liquidity: float
    confidence: float
    volatility: float
    foreign_demand: float
    calculated_rate: float
    previous_rate: float
    rate_change: float
    updated_at: str


class BehaviorSnapshotItem(ResponseModel):
    at: str
    active_players_count: int
    buy_tx_count: int
    sell_tx_count: int
    total_volume: float
    avg_net_worth: float
    gini_coefficient: float
    top10_wealth_share: float


class WorldEventItem(ResponseModel):
    event_id: str
    event_type: str
    scope: str
    title: str
    description: str
    effect_type: str
    effect_magnitude: float
    started_at: str
    ends_at: str
    is_active: bool
    affected_nation_name: str | None
    affected_currency: str | None


class ProposalListItem(ResponseModel):
    id: int
    proposer_username: str
    rule_key: str
    proposed_value: float
    status: str
    voting_opens_at: str
    voting_closes_at: str
    yes_count: int
    no_count: int
    abstain_count: int
    total_weight: float


class GovernanceLedgerItem(ResponseModel):
    id: int
    at: str
    actor_username: str | None
    action: str
    rule_key: str
    old_value: str | None
    new_value: str | None
    reason: str | None


class RuleOverrideItem(ResponseModel):
    id: int
    rule_key: str
    value: float
    scope: str
    target_id: int | None
    active_from: str
    active_until: str | None
    is_active: bool


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int
    page: int
    pages: int


class ActionResult(ResponseModel):
    success: bool
    affected: int
    message: str


# ── END OF responses.py ──
