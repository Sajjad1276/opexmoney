# OPEX MONEY Rebuild Phases 4–12 Execution

This document records the implementation contract for the remaining rebuild phases after the Phase 0–3 foundation.

## Phase 4 · Founder lifecycle
Founder drafts now have a durable lifecycle with expiration as a scheduled, idempotent operation. Finalization remains transactional and is still guarded by the database uniqueness constraints for active group and currency code.

Exit condition:
- stale active drafts are marked EXPIRED
- active/completed drafts are not touched
- finalization remains atomic

## Phase 5 · Nation resolver consolidation
Human nation context is resolved from authoritative Telegram-backed membership plus active game membership. AI nations remain virtual and do not require Telegram membership. Legacy home_nation, founder and holding fallbacks cannot independently create human citizenship during resolution or repair.

Exit condition:
- read-only resolution has no write side effects
- human repair requires active Telegram proof and row locks
- AI resolution remains virtual

## Phase 6 · Wallet + membership consistency
wallet_service.py is the canonical wallet reconciliation boundary. User.balance mirrors the active home-nation CurrencyHolding; XR remains its independent cash ledger. Membership transitions reconcile the wallet after activation and after leaving the last human nation.

Exit condition:
- joining a human nation creates/reuses the correct holding and synchronizes User.balance
- leaving/kicking does not leave a stale local balance projection

## Phase 7 · Market integration
Market nation lookup now goes through an active-nation service boundary. Inactive nations are rejected before trade execution; active human and AI nations remain tradable without changing trade formulas.

Exit condition:
- inactive currencies cannot enter buy/sell execution
- existing economic calculations remain unchanged

## Phase 8 · Economy event pipeline
A PostgreSQL-backed outbox stores economy events inside the same transaction that mutates the economy. Events are claimed with FOR UPDATE SKIP LOCKED, retried after failures and marked published separately. Membership transitions and trades emit durable events.

Exit condition:
- event creation is transactional with the source mutation
- publish failure does not lose the event
- duplicate event keys are idempotent

## Phase 9 · AI nation separation
The nation domain explicitly separates virtual AI nations from Telegram-backed human nations. Legacy group-less starter nations without founders are migrated to is_ai = true. The database and service layer enforce the boundary.

Exit condition:
- human nation requires a Telegram group
- AI nation does not have a Telegram group
- AI flows do not depend on Telegram membership projections

## Phase 10 · Governance / Treasury / War
Nation control now verifies both game role and Telegram membership for human nations. AI nations keep virtual control semantics. Treasury withdrawal and war declaration use the centralized nation-control authorization boundary.

Exit condition:
- stale human roles cannot perform privileged nation actions
- AI nation administration remains functional
- existing treasury and war economic rules are unchanged

## Phase 11 · UX rebuild hardening
The current UX surface is preserved while navigation contracts are centralized. Main ReplyKeyboard labels, More submenu callbacks and the home callback are tested as a stable interface contract.

Exit condition:
- keyboard factories and navigation tests agree
- no economy or gameplay rule is changed

## Phase 12 · Load + failure testing
Failure-oriented regression tests cover duplicate Telegram membership events and the new event outbox lifecycle. CI runs the full rebuild regression suite before the existing integration and full-suite gates.

Exit condition:
- duplicate membership events are idempotent
- outbox retry/publish state is durable
- all rebuild regression tests run in CI

## Non-goals
No new pricing formulas, no database reset, no removal of AI gameplay, no removal of governance/treasury/war, and no destructive rewrite of existing user balances.