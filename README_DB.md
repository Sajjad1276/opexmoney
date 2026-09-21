# OPEX MONEY Database Foundation v2

This is the unified SQLAlchemy database model set for the final game architecture.

## Model map

- Nation: Telegram group-backed nation, currency, exchange rate, treasury compatibility fields, AI marker, and soft-delete state.
- BotGroup: cached Telegram group metadata.
- NationTelegramMember: Telegram membership projection for nation groups.
- NationFoundingDraft: durable founder wizard state.
- User: player identity, balances, home nation, AI state, AI tier, and soft-delete state.
- CurrencyHolding: per-user local-currency holding.
- Transaction: executed buy, sell, or liquidation ledger entry.
- PriceAlert: user price alert. The old triggered attribute remains compatible with the new is_triggered column.
- UserActivity: activity events used by the live economy.
- NationMemberHistory: historical nation member-count samples.
- RateHistory: historical nation exchange-rate samples.
- TradePreview: short-lived trade quote record.
- NationRank: current nation rank snapshot.
- Proposal: governance proposal.
- Vote: proposal vote with weighted participation.
- RuleOverride: active and scheduled governance rule override.
- GovernanceLedger: governance audit ledger.
- PlayerTemporalProfile: player temporal-behavior profile.
- BehaviorSnapshot: aggregate player behavior and wealth snapshot.
- NationMembership: active and historical user-to-nation membership. NationMember remains a compatibility alias.
- NationLog: nation audit and event log.
- NationJoinRequest: private-nation join request with requested and resolved timestamps.
- NationInviteLink: one-time private-nation invite token.
- NationWar: nation-versus-nation war lifecycle.
- Mission: mission definition.
- UserMissionProgress: player mission progress.
- NationTreasury: treasury state used by the current services.
- TreasuryLog: treasury audit ledger.
- Lesson: academy lesson.
- UserLessonProgress: per-player lesson progress.
- UserXP: academy experience and level.
- CurrencyMarketState: live pressure, liquidity, confidence, demand, activity, and calculated rate for a currency.
- PriceMovementReceipt: immutable cause receipt for a rate change.
- WorldEvent: live world event with scope, source, duration, and economic effect.
- AIUsageLog: daily per-user AI feature usage counters.
- DecisionSnapshot: Shadow Ledger decision and opportunity-cost record.
- ShopItem: purchasable item definition.
- UserPurchase: player purchase and entitlement record.

## Key relationships

- User.home_nation_id -> Nation.nation_id.
- Nation.founder_user_id -> User.user_id.
- NationMembership.user_id -> User.user_id.
- NationMembership.nation_id -> Nation.nation_id.
- NationInviteLink.created_by and used_by -> User.user_id.
- NationJoinRequest.user_id and resolved_by -> User.user_id.
- NationJoinRequest.nation_id -> Nation.nation_id.
- WorldEvent.affected_nation_id -> Nation.nation_id.
- DecisionSnapshot.transaction_id -> Transaction.id.
- DecisionSnapshot.user_id -> User.user_id.
- UserPurchase.item_key -> ShopItem.item_key.
- UserPurchase.user_id -> User.user_id.
- AIUsageLog.user_id -> User.user_id.

Membership invariant: PostgreSQL enforces at most one active NationMembership row per user with a partial unique index on user_id WHERE is_active = TRUE. The legacy nation_id plus user_id unique constraint remains for compatibility with the current service layer.

User.telegram_id is an ORM synonym for users.user_id. The current schema already uses the Telegram user ID as the primary key, so the requested Telegram index does not require a second identity column.

Nation and User use deleted_at for soft deletion. No real delete is required for normal lifecycle operations.

All timestamp columns are stored in PostgreSQL as timezone-aware UTC timestamps. The ORM UTCDateTime adapter also accepts legacy naive UTC datetime inputs and returns UTC-aware values that remain compatible with the current service layer. All declared relationships use lazy=selectin.

## Required indexes

- ix_users_telegram_id
- ix_nation_members_user_active
- uq_nation_members_active_user
- ix_currency_market_state_currency_code
- ix_world_events_active_ends_at
- ix_decision_snapshots_user_evaluated
- ix_price_alerts_user_triggered
- ix_ai_usage_logs_user_date

Supporting indexes from the current schema remain in place for trade, market, governance, nation, treasury, academy, membership, alerts, and event queries.

## Migration location

alembic.ini configures script_location = alembic. Therefore the live migration is stored at alembic/versions/0019_foundation_v2.py.

The repository has a separate migrations directory, but Alembic does not read it. A duplicate migration there would create a dead second graph and is intentionally not created.

## Compatibility

- Existing imports of NationMember continue to work.
- Existing code that reads or writes PriceAlert.triggered continues to work.
- Existing code that reads NationJoinRequest.created_at, reviewed_at, or reviewed_by continues to work through SQLAlchemy synonyms.
- Existing User.user_id remains the Telegram identity.
- session.py, Redis FSM, APScheduler, and Railway configuration are not changed by this database commit.