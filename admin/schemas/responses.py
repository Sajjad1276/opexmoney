from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class StatsOverview(ORMModel):
    total_players: int
    active_players_24h: int
    total_nations: int
    active_wars: int
    transactions_today: int
    transaction_volume_today: Decimal
    total_market_volume: Decimal
    pending_proposals: int
    new_players_7d: int
    gini_coefficient: float
    top10_wealth_share: float


class EconomyHealth(ORMModel):
    inflation_rate: float
    market_confidence: float
    total_liquidity: float
    avg_volatility: float
    wealth_concentration: float
    market_stability: float


class PageInfo(ORMModel):
    total: int
    page: int
    pages: int


class PlayerListItem(ORMModel):
    user_id: int
    username: str
    balance: Decimal
    xr_balance: Decimal
    role: str
    ai_tier: str
    home_nation_name: str | None
    home_nation_flag: str | None
    created_at: datetime
    is_ai: bool
    total_transactions: int
    last_activity_at: datetime | None


class PlayersPage(PageInfo):
    items: list[PlayerListItem]


class PlayerTransaction(ORMModel):
    id: int
    nation_id: int
    nation_name: str | None = None
    transaction_type: str
    spend_xr: Decimal
    amount: Decimal
    fee_xr: Decimal
    rate: Decimal
    created_at: datetime


class PlayerMissionSummary(ORMModel):
    completed: int


class PlayerWarSummary(ORMModel):
    war_id: int
    status: str
    nation_id: int
    opponent_nation_id: int
    opponent_name: str | None
    opponent_flag: str | None
    ends_at: datetime


class PlayerAIUsage(ORMModel):
    date: date
    advisor_questions_used: int
    portfolio_scans_used: int
    war_analysis_used: int


class PlayerXP(ORMModel):
    total_xp: int
    level: str


class HomeNationDetails(ORMModel):
    nation_id: int
    name: str
    flag_emoji: str | None
    currency_code: str
    exchange_rate: Decimal
    member_count: int
    is_active: bool
    is_ai: bool


class PlayerProfile(ORMModel):
    user_id: int
    username: str
    home_nation_id: int | None
    balance: Decimal
    xr_balance: Decimal
    role: str
    is_ai: bool
    ai_strategy: str
    ai_tier: str
    deleted_at: datetime | None
    created_at: datetime
    home_nation: HomeNationDetails | None
    last_transactions: list[PlayerTransaction]
    mission_completion_count: int
    current_war: PlayerWarSummary | None
    ai_usage_today: PlayerAIUsage | None
    total_xp: int
    level: str


class NationListItem(ORMModel):
    nation_id: int
    name: str
    flag_emoji: str | None
    currency_code: str
    exchange_rate: Decimal
    rate_change_24h: float
    member_count: int
    treasury: Decimal
    trade_volume_24h: Decimal
    nation_rank: int | None
    is_active: bool
    join_policy: str
    is_ai: bool
    created_at: datetime
    active_members_24h: int
    war_status: str


class NationsPage(ORMModel):
    items: list[NationListItem]
    total: int
    page: int
    pages: int


class NationRateHistoryPoint(ORMModel):
    id: int
    rate: Decimal
    volume: Decimal
    active_members: int
    calculated_at: datetime
    dominant_cause: str | None
    pressure_signal: Decimal | None
    foreign_signal: Decimal | None
    activity_score: Decimal | None
    trade_score: Decimal | None
    growth_score: Decimal | None


class NationMemberItem(ORMModel):
    user_id: int
    username: str
    balance: Decimal
    xr_balance: Decimal
    role: str
    is_ai: bool


class TreasuryLogItem(ORMModel):
    id: int
    actor_id: int | None
    actor_username: str | None
    action: str
    amount_xr: Decimal
    note: str | None
    created_at: datetime


class NationWarItem(ORMModel):
    id: int
    status: str
    nation_id: int
    nation_name: str
    nation_flag: str | None
    nation_member_count: int
    nation_treasury: Decimal
    opponent_nation_id: int
    opponent_name: str
    opponent_flag: str | None
    opponent_member_count: int
    opponent_treasury: Decimal
    declared_at: datetime
    ends_at: datetime
    ended_at: datetime | None


class NationDetail(ORMModel):
    nation_id: int
    group_id: int | None
    name: str
    flag_emoji: str | None
    currency_code: str
    founder_user_id: int | None
    exchange_rate: Decimal
    rate_prev: Decimal
    rate_24h_open: Decimal
    trade_volume_24h: Decimal
    active_members_24h: int
    nation_rank: int | None
    last_rate_update: datetime | None
    member_count: int
    is_active: bool
    join_policy: str
    personality: str
    is_ai: bool
    invite_code: str
    treasury: Decimal
    deleted_at: datetime | None
    created_at: datetime
    rate_history: list[NationRateHistoryPoint]
    top_members: list[NationMemberItem]
    treasury_logs: list[TreasuryLogItem]
    active_wars: list[NationWarItem]


class MarketStateItem(ORMModel):
    currency_code: str
    nation_name: str | None
    buy_pressure: float
    sell_pressure: float
    liquidity: float
    confidence: float
    volatility: float
    foreign_demand: float
    national_activity: float
    calculated_rate: Decimal
    previous_rate: Decimal
    updated_at: datetime


class RateHistoryItem(ORMModel):
    id: int
    nation_id: int
    nation_name: str | None
    rate: Decimal
    volume: Decimal
    active_members: int
    calculated_at: datetime
    dominant_cause: str | None
    pressure_signal: Decimal | None
    foreign_signal: Decimal | None
    activity_score: Decimal | None
    trade_score: Decimal | None
    growth_score: Decimal | None


class BehaviorSnapshotItem(ORMModel):
    id: int
    at: datetime
    active_players_count: int
    buy_tx_count: int
    sell_tx_count: int
    export_tx_count: int
    import_tx_count: int
    total_volume: Decimal
    avg_net_worth: Decimal
    median_net_worth: Decimal
    gini_coefficient: Decimal
    top10_wealth_share: Decimal


class WorldEventItem(ORMModel):
    event_id: UUID
    event_type: str
    scope: str
    affected_nation_id: int | None
    affected_currency: str | None
    title: str
    description: str
    effect_type: str
    effect_magnitude: float
    duration_minutes: int
    started_at: datetime
    ends_at: datetime
    is_active: bool
    source: str
    announced_in_group: bool


class WarListItem(ORMModel):
    id: int
    status: str
    nation_id: int
    nation_name: str
    nation_flag: str | None
    nation_member_count: int
    nation_treasury: Decimal
    opponent_nation_id: int
    opponent_name: str
    opponent_flag: str | None
    opponent_member_count: int
    opponent_treasury: Decimal
    declared_at: datetime
    ends_at: datetime
    ended_at: datetime | None


class ProposalItem(ORMModel):
    id: int
    created_at: datetime
    proposer_player_id: int
    proposer_username: str | None
    rule_key: str
    proposed_value: Decimal
    target_scope: str
    target_id: int | None
    status: str
    voting_opens_at: datetime
    voting_closes_at: datetime
    effective_from: datetime | None
    effective_until: datetime | None
    yes_votes: int
    no_votes: int
    abstain_votes: int
    total_weight: Decimal


class ProposalsPage(ORMModel):
    items: list[ProposalItem]
    total: int
    page: int
    pages: int


class GovernanceLedgerItem(ORMModel):
    id: int
    at: datetime
    actor_player_id: int | None
    actor_username: str | None
    action: str
    rule_key: str
    old_value: str | None
    new_value: str | None
    reason: str | None


class GovernanceLedgerPage(ORMModel):
    items: list[GovernanceLedgerItem]
    total: int
    page: int
    pages: int


class RuleOverrideItem(ORMModel):
    id: int
    rule_key: str
    value: Decimal
    scope: str
    target_id: int | None
    source_proposal_id: int | None
    active_from: datetime
    active_until: datetime | None
    suspended_until: datetime | None
    is_active: bool


class TransactionItem(ORMModel):
    id: int
    user_id: int
    username: str | None
    nation_id: int
    nation_name: str | None
    transaction_type: str
    spend_xr: Decimal
    amount: Decimal
    fee_xr: Decimal
    rate: Decimal
    created_at: datetime


class TransactionsPage(ORMModel):
    items: list[TransactionItem]
    total: int
    page: int
    pages: int


class BroadcastActionResponse(ORMModel):
    sent: int
    failed: int


class UpdatedActionResponse(ORMModel):
    updated: int


class SuccessActionResponse(ORMModel):
    success: bool


class ErrorActionResponse(ORMModel):
    success: bool
    error: str | None = None


class AdminActionReceipt(ORMModel):
    success: bool = True
    updated: int = 0
    sent: int = 0
    failed: int = 0
    message: str | None = None
