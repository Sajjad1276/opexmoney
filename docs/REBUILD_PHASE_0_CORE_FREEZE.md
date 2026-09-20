# OPEX MONEY - Core Rebuild Phase 0: Feature Freeze

## Status

- Phase: 0
- Purpose: freeze the product surface and establish a verified architecture baseline before core reconstruction.
- Baseline commit: c3b5fd5b16d969bba1cee79e7653c020cddcbdfb
- Baseline date: 2026-09-20
- Working branch: rebuild/phase-0-core-freeze
- Production baseline: Railway deployment from the baseline commit was reported as SUCCESS.
- This phase does not deploy to production.

## Product Invariant

OPEX MONEY is a Telegram-native economic world.

For human nations:

Telegram Group = Nation
Telegram Group Members = Citizens
Nation Currency = Currency of that group
Telegram Membership = Source of truth for human membership
Database = Persistent projection/state, not the authority for Telegram membership

The database must not create or preserve active human citizenship independently of Telegram membership.

AI nations are a deliberate exception:

AI Nation = virtual nation with simulated membership
Human Nation = Telegram-backed nation

The distinction must be explicit in the domain model.

## Frozen Product Surface

Until the end of the core rebuild, do not introduce new product capabilities in these areas:

- Academy
- New governance features
- New treasury features
- New war/combat features
- New missions
- New AI-world capabilities
- New social features
- New economic rules
- Major UX redesigns
- New nation-management subsystems

Existing production behavior may be repaired when required for correctness, security, data integrity, or compatibility with the core rebuild.

## Allowed Work During Freeze

Only these categories are in scope:

1. Core-domain reconstruction.
2. Telegram membership synchronization.
3. Nation identity and membership consistency.
4. Founder lifecycle correctness.
5. User/nation resolver consolidation.
6. Wallet and holding consistency caused by membership changes.
7. Market integration required by the new membership model.
8. Economic event plumbing required by the current game model.
9. Human-vs-AI nation separation.
10. Tests, diagnostics, migrations, data repairs, and deployment safety required by the above.

## Current Architecture Baseline

The current repository already contains:

- Nation with group_id.
- NationMember as an internal membership table.
- NationFoundingDraft for founder workflow state.
- NationJoinRequest for approval-based internal joining.
- BotGroup for Telegram group metadata.
- CurrencyHolding for user/nation balances.
- NationLog for nation events.
- AI nations with group_id = NULL and is_ai = TRUE.
- A resolver in app/services/nation_service.py that currently derives active nation context from multiple database fallbacks.
- _join_user() in app/handlers/nation_management.py that currently creates internal membership directly.
- Founder group verification in app/handlers/founder.py.

These are baseline facts, not target architecture.

## Known Core Deviations

### 1. Internal join can create citizenship without Telegram membership

_join_user() currently creates NationMember, increments Nation.member_count, and sets User.home_nation_id without using Telegram group membership as the authoritative prerequisite.

### 2. Human member count is internally mutated

Nation.member_count is changed by game join logic instead of being derived from synchronized active Telegram membership.

### 3. Human membership lifecycle is incomplete

The current system handles bot membership changes via my_chat_member, but does not yet implement a complete citizen lifecycle based on chat_member events.

### 4. Nation resolution has multiple fallbacks

The current resolver can fall back through home nation, membership, founder relationship, and holdings. This is useful for repair compatibility but is not the final source-of-truth model.

### 5. Human and AI nations share one table without an explicit domain boundary

AI nations currently use Nation.group_id = NULL. The rebuilt architecture must make the distinction explicit rather than relying on incidental NULL semantics.

## Non-Goals of Phase 0

Phase 0 does not:

- rewrite the market engine;
- reset production data;
- delete existing nation/member tables;
- remove AI nations;
- change game economy numbers;
- redesign the entire Telegram UI;
- deploy the rebuild branch.

## Exit Criteria

Phase 0 is complete when all of the following remain true:

- The baseline commit is recorded.
- The feature surface is frozen.
- Human membership source-of-truth is explicitly defined.
- Human and AI nation semantics are explicitly defined.
- Known architectural deviations are recorded.
- Core rebuild work can proceed without adding unrelated features.
- Production remains on the pre-rebuild baseline until a later migration phase is explicitly approved.

## Phase 1 Entry Condition

Phase 1 begins with the Membership Domain and Telegram synchronization design. No unrelated feature work should enter the branch before that work is complete.