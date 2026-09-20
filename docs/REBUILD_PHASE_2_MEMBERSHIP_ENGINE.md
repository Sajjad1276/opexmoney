# OPEX MONEY - Phase 2: Telegram Membership Engine

## Baseline

- Branch: `rebuild/phase-2-membership-engine`
- Phase 0 commit: `bacec6ecc95d37e722d12b805a55501292ff7ae6`
- Phase 2 review head at the time of this document: `9d1c24b6e307788cde0a8f86627db7f67ca3ea73`
- Production deployment: none

## Implemented

### Telegram membership projection
`nation_telegram_members` stores Telegram membership independently of `users` so a Telegram member can exist before starting the bot.

### Event-driven synchronization
`app/handlers/membership.py` consumes `chat_member` updates for human-backed nations and writes the membership projection.

### Human join rule
For human nations, `_join_user()` now verifies actual Telegram membership before creating or reactivating game membership.

### Human kick rule
The management kick operation now removes the user from the Telegram group. Database membership state is changed by the resulting Telegram membership event.

### Legacy approval bridge
Pending approval requests for human nations are only accepted after Telegram membership is verified. New human membership is created by the Telegram sync service rather than by the approval handler.

### Registration reconciliation
When a user completes trader registration, active Telegram memberships already known to OPEX are projected into `NationMember` and `CurrencyHolding`.

### Member-count reconciliation
Human nation counts are refreshed from Telegram every 15 minutes. Event-driven deltas handle changes between reconciliations.

### Founder bootstrap
Founder nation creation seeds the founder's Telegram membership projection in the same database transaction as nation creation.

### Kick economy preservation
Existing kick behavior that liquidates the member's local holding to the global dollar ledger is now triggered from a Telegram `kicked` membership transition for human nations.

### AI separation
AI nations retain the previous virtual-world membership path because they intentionally have no Telegram group.

## Migration

`0017_telegram_membership` creates the new membership projection table.

`scripts/repair_alembic_state.py` recognizes `0017_telegram_membership` so Railway production repair logic cannot stamp an older revision after the new table exists.

## Explicit non-goals

- No changes to trading formulas.
- No new economic rules.
- No deletion of legacy `NationMember` data.
- No production deployment.
- No attempt to enumerate every historical Telegram group member through Telegram.

## Transitional limitation

Existing human nations may contain legacy `NationMember` rows that predate the Telegram projection. The new system avoids counting those rows twice on their first Telegram event, but full historical membership reconciliation remains a later migration/reconciliation task.

Human membership repair through `nation_service.py` is now blocked unless an active Telegram membership projection exists. Legacy active `NationMember` rows are still readable for compatibility until the resolver phase is completed.

## Validation status

GitHub Actions did not return a workflow run for the temporary syntax-check workflow, so no CI pass is claimed. The branch was statically reviewed through the repository diff after implementation.

## Next dependency

The next core phase must consolidate nation resolution around the Telegram membership projection and remove remaining human-member legacy fallbacks without breaking existing production identities.