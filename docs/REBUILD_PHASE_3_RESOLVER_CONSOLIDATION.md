# OPEX MONEY - Core Rebuild Phase 3: Nation Resolver Consolidation

## Status

- Phase: 3
- Scope: consolidate active-nation resolution around the canonical membership domain.
- Base implementation: `rebuild/phase-2-membership-engine`
- Working branch: `rebuild/phase-3-resolver-consolidation`
- Production deployment: not yet performed from the Phase 3 branch.

## Problem Closed by Phase 3

Before Phase 3, `get_user_active_nation_context()` could resolve a Human Nation from several independent database signals:

- `home_nation_id`
- active `NationMember`
- founder ownership
- `CurrencyHolding`

That allowed stale internal state to recreate human citizenship without proof of current Telegram membership.

## Canonical Resolver Contract

### Human Nation

A Human Nation resolves only when:

1. the nation is active;
2. the nation is not an AI nation;
3. an active `NationTelegramMember` exists for the user and nation;
4. an active `NationMember` exists, or explicit repair is requested under a transaction lock.

Without active Telegram membership, the resolver returns no Human Nation even when stale founder, holding, home-nation, or legacy membership rows remain.

### AI Nation

AI nations remain virtual.

They do not require:

- Telegram membership;
- `NationTelegramMember`;
- a Telegram group.

AI resolution continues through active virtual `NationMember` state, with legacy founder/holding repair retained only inside the AI domain.

## Repair Rules

Repair is explicitly transactional:

- `repair=True` requires `lock=True`.
- Human repair is permitted only after active Telegram membership is proven.
- Human founder repair creates founder membership only when Telegram membership is active.
- Human citizen repair creates citizen membership only when Telegram membership is active.
- AI repair follows virtual-world semantics.

Read-only resolution performs no writes.

## Resolver Priority

For a user with a stored home nation:

1. validate that home nation against its domain rules;
2. for Human Nation, require active Telegram membership;
3. for AI Nation, require active virtual game membership.

If home context is unusable, the resolver searches active human Telegram-backed memberships.

After human resolution, AI virtual memberships are considered.

AI founder/holding fallbacks remain isolated inside the AI domain.

## Explicitly Removed for Human Nations

The following can no longer independently establish active Human Nation context:

- founder ownership alone;
- local currency holdings alone;
- stale `home_nation_id`;
- active legacy `NationMember` without Telegram proof.

## Compatibility

Phase 3 does not delete:

- legacy `NationMember` rows;
- `CurrencyHolding` rows;
- existing users;
- existing nations;
- AI-world data.

It changes only the authority used to resolve active nation context.

## Validation Added

`tests/test_nation_resolver_phase3.py` covers:

- stale human membership without Telegram proof;
- valid human membership with active Telegram projection;
- human membership repair with Telegram proof;
- AI membership without Telegram;
- blocking human founder/holding fallback paths.

## Non-Goals

Phase 3 does not:

- change trading formulas;
- change exchange-rate rules;
- modify wallet economics;
- redesign Telegram UI;
- remove legacy data;
- enumerate historical Telegram members;
- introduce new gameplay systems.

## Exit Condition

Phase 3 is complete when all nation-context reads and repairs use the canonical resolver semantics above, and CI passes the complete test suite with no stale Human Nation fallback behavior.
