# OPEX MONEY - Core Rebuild Phase 1: Membership Domain

## Status

- Phase: 1
- Purpose: define and lock the canonical membership domain before membership-engine implementation.
- Base: `main` after Phase 0
- Working branch: `rebuild/phase-1-membership-domain`
- Production deployment: this phase may be deployed as a contract/documentation change only; it must not activate Phase 2 runtime code.
- Scope: domain contract, source-of-truth rules, lifecycle semantics, synchronization boundaries, and compatibility constraints.

## Phase 1 Boundary

Phase 1 does not implement the membership engine.

Phase 1 defines the contract that the membership engine must implement in Phase 2.

No new gameplay feature, economic rule, UI redesign, governance capability, academy feature, mission feature, or AI-world capability belongs in this phase.

## Canonical Domain Model

OPEX MONEY has two nation types.

### Human-backed Nation

A human-backed nation is identified by a real Telegram group.

The invariants are:

- Telegram Group = Nation
- Telegram Group Members = Citizens
- Nation Currency = Currency belonging to that nation
- Telegram membership = authoritative source for human citizenship
- Database = persistent projection and game state
- An active human citizenship record must not exist solely because a database row exists.

The Telegram group is therefore the external authority for whether a person currently belongs to a human nation.

### AI Nation

An AI nation is a deliberately virtual exception.

The invariants are:

- No Telegram group is required.
- Membership is simulated by the game.
- AI membership does not require Telegram membership.
- Human membership rules must never be silently applied to AI nations.

The distinction between human and AI nations must be explicit in domain logic.

## Membership Entities

The rebuilt domain separates three concepts.

### 1. Telegram membership projection

Represents what OPEX knows about a Telegram user's membership in a human nation's group.

Required identity:

- nation_id
- telegram_user_id

Required state:

- Telegram status
- whether the user is currently a member
- whether the projected membership is active
- joined timestamp
- left timestamp
- last observed timestamp

This projection deliberately must not require a row in the OPEX users table because a Telegram group member can exist before starting the bot.

### 2. Game membership

Represents the user's playable citizenship inside OPEX.

It may contain:

- role
- active/inactive state
- nation relationship
- game-specific membership metadata

For human nations, game membership may only be active when authoritative Telegram membership permits it.

For AI nations, game membership is governed by the virtual-world rules.

### 3. User account

Represents the user's OPEX identity and wallet-facing state.

User registration is not proof of Telegram citizenship.

The system must not use registration state, FSM state, username presence, wallet balance, or home_nation_id as a substitute for authoritative Telegram membership.

## Source-of-Truth Rules

### Human nations

The authority chain is:

Telegram membership
→ Telegram membership projection
→ game membership projection
→ user-facing nation context and related derived state

The database must not reverse this chain and declare Telegram membership from internal game rows.

### AI nations

The authority chain is:

AI/world simulation state
→ game membership projection
→ user-facing nation context

Telegram state is irrelevant to AI membership.

## Telegram Membership Lifecycle

The membership engine must model real Telegram transitions.

### Active states

The Telegram statuses that count as active citizenship are:

- member
- administrator
- creator

Restricted users require explicit interpretation of the `is_member` flag rather than relying on status alone.

### Inactive states

A user is inactive for human citizenship when Telegram indicates that the user is no longer a member, including:

- left
- kicked

A restricted user remains active only when Telegram reports that the user is still a member.

### State transition semantics

The engine must distinguish:

- first observation
- active → active observation
- inactive → active rejoin
- active → inactive leave/kick

Only a real transition into or out of active membership may produce a membership-change event.

Repeated observations of the same active state must be idempotent.

## Event Contract

The primary Telegram lifecycle input is the `chat_member` update.

The handler must:

1. identify the affected Telegram group;
2. resolve that group to a human nation;
3. interpret the new Telegram member state;
4. persist the Telegram membership projection;
5. synchronize the game-membership projection when the user is an OPEX-registered player;
6. preserve membership state even when the user has never started the bot;
7. keep AI nations outside this path.

The handler must not make database membership authoritative over Telegram.

## Registered vs Unregistered Telegram Members

A Telegram user can be a real citizen before becoming an OPEX user.

Therefore:

- Telegram membership must be stored independently of the users table.
- Missing OPEX registration must not erase the Telegram membership projection.
- Registration later reconciles the existing active Telegram membership into game state.
- A failed or absent OPEX registration must not manufacture a Telegram membership.

This is required to preserve the Telegram group as the real citizen population.

## Nation Member Count

For human nations, `Nation.member_count` is derived state.

The canonical population is the Telegram group population, not an internally incremented game counter.

The system therefore requires:

- event-driven changes for near-real-time transitions;
- periodic reconciliation against Telegram group membership count;
- no independent game-only increments that claim a human citizen exists.

For AI nations, member count remains virtual-world state.

## Join and Approval Semantics

A human-nation join operation must verify actual Telegram membership before activating game citizenship.

An approval request cannot override Telegram membership.

For human nations:

- no Telegram membership -> no active game citizenship;
- Telegram membership verified -> game membership may be synchronized;
- the synchronization service, not a legacy approval handler, is responsible for creating the canonical human membership projection.

For AI nations, the legacy virtual membership path remains valid.

## Kick and Leave Semantics

For a human nation, game-level kick must cause the actual Telegram membership to be removed.

The desired sequence is:

game kick request
→ Telegram removal
→ Telegram `chat_member` event
→ membership projection transition
→ game membership deactivation
→ economy-side membership cleanup

The game database must not mark a human citizen inactive merely as a substitute for successfully removing them from Telegram.

Telegram remains the trigger for the authoritative human membership transition.

## Founder Semantics

The founder of a human nation is a real Telegram citizen.

Founder bootstrap must therefore establish both:

- the game founder membership;
- the corresponding Telegram membership projection.

Founder status does not create an exception to Telegram membership authority.

AI founders are governed by the virtual-world exception.

## Wallet and Holding Consistency

Membership transitions may require changes to nation-local balances or holdings.

The membership domain does not redefine the economic rule.

It defines only the trigger boundary:

- human leave/kick transition is detected from authoritative Telegram state;
- the existing approved economic cleanup is executed from that transition;
- trading formulas and economy numbers remain unchanged.

No economic rule may be invented as part of Phase 1.

## Resolver Contract

The nation resolver is transitional during the rebuild.

Target behavior:

- human nation context must ultimately be resolved from active Telegram-backed membership;
- legacy `home_nation_id`, founder, holding, and legacy `NationMember` fallbacks are compatibility mechanisms, not authoritative sources;
- AI nations remain resolvable through their virtual membership path.

The final resolver must never repair an active human membership solely from a wallet, home-nation pointer, or stale internal membership row when Telegram membership is absent.

## Transaction and Consistency Rules

Membership synchronization must obey these rules:

- membership state changes are atomic at the database transaction boundary;
- concurrent transitions for the same nation/user pair must be serialized;
- duplicate Telegram events must be idempotent;
- active membership must not be double-counted;
- user projection creation and membership projection updates must not create partial active citizenship states;
- Telegram API calls must not be held open inside database transactions unless explicitly required by the framework and proven safe.

## Backward Compatibility

The existing `NationMember` table remains readable during the transition.

Phase 1 does not:

- delete legacy membership rows;
- rewrite existing economy values;
- reset production identities;
- remove AI nations;
- require historical Telegram enumeration;
- migrate all historical membership data immediately.

Legacy data is preserved so that Phase 2 and later resolver work can migrate safely.

## Explicit Non-Goals

Phase 1 does not:

- implement `nation_telegram_members`;
- add the `chat_member` handler;
- add membership scheduler jobs;
- change production nation counts;
- change trading formulas;
- introduce new economic rules;
- redesign Telegram UI;
- deploy the Phase 2 membership engine.

## Phase 1 Exit Criteria

Phase 1 is complete when the following contract is fixed:

1. Human Telegram membership is the authoritative citizen source.
2. AI membership is explicitly virtual and separate.
3. Telegram membership is representable independently of OPEX registration.
4. Human membership transitions are defined as event-driven and idempotent.
5. Human join, approval, leave, and kick semantics are defined around Telegram authority.
6. Human population count is defined as derived Telegram-backed state.
7. Founder, wallet, holding, and resolver boundaries are defined without changing economy rules.
8. Phase 2 can implement the membership engine without changing the domain contract.

## Phase 2 Entry Condition

Phase 2 may begin only after this contract is accepted.

Phase 2 implementation must follow this document rather than introducing a competing membership source of truth.
