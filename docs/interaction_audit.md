# OPEX MONEY Phase 4 Interaction Audit

Repository: `Sajjad1276/opexmoney`
Branch: `phase4-core-ux-audit`
Baseline commit: `c04f724ef556a03478fa9d6dffa03bc7cb5f95ea`

> این سند baseline قبل از هر تغییر کد است. PASS نهایی فقط بعد از اجرای harness واقعی ثبت می‌شود.

## نتیجه سریع

| حوزه | یافته baseline | تصمیم |
|---|---|---|
| FSM storage | `main.py` در نبود `REDIS_URL` به `MemoryStorage` برمی‌گردد | اصلاح و fail-fast در production |
| Founder wizard | `group_id`، `nation_name` و `currency_code` فقط در FSM نگهداری می‌شوند | `OnboardingDraft` |
| Keyboard | handlerها مستقیم Reply/Inline markup ارسال می‌کنند | `KeyboardStateManager` |
| Intent | stateها registry مرکزی و validator مشترک ندارند | `StepDefinition` + `intent_router` |
| Legacy UI | `market_chart`، `create_nation` و trade-confirmation legacy مصرف فعال ندارند | حذف |
| Placeholder UI | `sections` و بخشی از nation فقط پیام توسعه می‌دهند | پیام صادقانه یا حذف گزینه |

## Part A.1 Handler inventory

### start.py

| Handler | Trigger | State | Baseline | تصمیم |
|---|---|---|---|---|
| `start` | `/start` | any | DB-driven onboarding/dashboard | ✅* |
| `start_game_button` | `🎮 شروع بازی` | none | shared registration flow | ✅* |
| `start_game_callback` | `start_game` | none | same shared flow | ✅* |
| `start_help` | `❓ راهنما` | any | real help text | ✅* |
| `start_help_callback` | `show_help` | None | real help text | ⚠️ integrate with intent |
| `join_nation` | `join_nation:<id>` | SELECT_NATION | real User/Holding/Activity DB write | ✅* |
| `first_trade_tutorial` | `first_trade_tutorial` | FSM flag | reads real DB | ⚠️ flag is FSM-only |
| `confirm_first_trade` | `confirm_first_trade` | FSM flag | real Transaction and balances | ✅* |
| `skip_first_trade` | `skip_first_trade` | FSM flag | clears wizard and returns menu | ✅* |
| `cancel_start` | `cancel_start` | any | clears state but does not centrally restore keyboard | ⚠️ keyboard manager |

### onboarding_fix.py

| Handler | Trigger | State | Baseline | تصمیم |
|---|---|---|---|---|
| `restart_onboarding_with_command` | `/start` | SET_USERNAME_PLAYER | restarts registration | ✅* |
| `restart_onboarding_with_text_command` | start text aliases | SET_USERNAME_PLAYER | restarts registration | ✅* |
| `reject_blocked_name` | blocked name | SET_USERNAME_PLAYER | validation message | ✅* |
| `reject_non_english_name` | invalid name | SET_USERNAME_PLAYER | validation message | ✅* |
| `accept_valid_name` | valid name | SET_USERNAME_PLAYER | persists User and temporal profile | ✅* |
| `cancel_start_fix` | `cancel_start` | SET_USERNAME_PLAYER | clears state, no keyboard restore | ⚠️ keyboard manager |

### founder.py

| Handler | Trigger | State | Baseline | تصمیم |
|---|---|---|---|---|
| `start_founder` | `found_nation` | None/SELECT_NATION | starts founder wizard | ✅* |
| `bot_group_status_changed` | my_chat_member | WAITING_GROUP_ADMIN | persists BotGroup | ✅* |
| `group_founder_start` | group `/start founder_<id>` | WAITING_GROUP_ADMIN | continues group flow | ✅* |
| `receive_nation_name` | text | SET_NATION_NAME | stores name/code only in FSM | ⚠️ persistent draft |
| `confirm_founder` | `confirm_found` / legacy alias | CONFIRM | real atomic nation creation | ✅* |
| `cancel_founder` | `cancel_founder` | founder states | clears state and sends reply menu | ⚠️ keyboard manager |

### market.py

| Handler | Trigger | State | Baseline | تصمیم |
|---|---|---|---|---|
| `market_button` | `💹 بازار` | menu | real market render | ⚠️ reply/inline conflict |
| `market_main` | `market_main` | any | real market render | ✅* |
| `market_refresh` | `market_refresh` | any | real DB refresh | ✅* |
| `market_buy` | `market_buy` | any | starts buy flow | ✅* |
| `buy_currency` | `buy_<id>` | any | enters WAITING_BUY_AMOUNT | ✅* |
| `buy_quick` | `buyq_*` | buy | DB preview | ✅* |
| `buy_amount_message` | free text | WAITING_BUY_AMOUNT | DB preview | ✅* |
| `confirm_buy` | `cbuy_*` | preview | real atomic trade | ✅* |
| `market_sell` | `market_sell` | any | starts sell flow | ✅* |
| `sell_currency` | `sell_<code>` | any | enters WAITING_SELL_AMOUNT | ✅* |
| `sell_quick` | `sellq_*` | sell | DB preview | ✅* |
| `sell_amount_message` | free text | WAITING_SELL_AMOUNT | DB preview | ✅* |
| `confirm_sell` | `csell_*` | preview | real atomic trade | ✅* |
| `market_chart` | `market_chart` | any | placeholder; no active keyboard producer | 🗑 delete |
| `market_history` | `market_history` | any | reads real Transaction rows | ✅* |

### nation.py

| Handler | Trigger | Baseline | تصمیم |
|---|---|---|---|
| `open_nations` | `🌍 ملت‌ها` | real read, direct inline markup | ⚠️ keyboard manager |
| `back_to_dashboard` | `back_to_dashboard` | real dashboard | ✅* |
| `my_nations` | `my_nations` | real read plus placeholder text | ⚠️ remove placeholder |
| `explore_nations` | `explore_nations` | real read, direct inline markup | ⚠️ keyboard manager |
| `unavailable_nation_panel` | `founder_panel` / `create_nation` | only alert | ⚠️/🗑 | remove producer-less legacy path |
| `unavailable_trade_confirmation` | `confirm_trade` / `cancel_trade` | legacy alert only | 🗑 delete |

### sections.py

| Handler | Trigger | Baseline | تصمیم |
|---|---|---|---|
| `portfolio` | `📊 پورتفولیو` | placeholder | ⚠️ honest temporary message |
| `missions` | `⚡ مأموریت` | placeholder | ⚠️ honest temporary message |
| `ranking` | `🏆 رتبه‌بندی` | placeholder | ⚠️ honest temporary message |
| `settings` | `⚙️ تنظیمات` | placeholder | ⚠️ honest temporary message |

### governance.py

All live governance callbacks map to real DB-backed actions. The main Phase 4 finding is persistence: `rule_key`, `proposed_value`, `target_scope`, `target_id` and `current_value` live in FSM until confirmation.

| State handler | Baseline | تصمیم |
|---|---|---|
| `governance_select_rule` | real rule registry | ✅* |
| `governance_receive_value` | real validation, FSM-only draft | ⚠️ persistent draft |
| `governance_confirm` | real Proposal write | ✅* |
| vote/history/revoke handlers | real DB-backed operations | ✅* |

## Part A.3 Callback audit

Current keyboard callbacks have an owner. No current producer is left completely unhandled.

Suspicious legacy items:

1. `founder_panel`: current founder menu displays it, but handler only says not active. Decision: remove the button and handler in this phase.
2. `create_nation`: no active keyboard producer. Decision: delete.
3. `confirm_trade` / `cancel_trade`: only in unused legacy keyboard. Decision: delete.
4. `market_chart`: handler exists but current market keyboard does not create it. Decision: delete.
5. `cancel_start`: two handlers exist, one generic and one onboarding-specific. Decision: one central cancellation path.

## Part B FSM persistence

Confirmed from current `main.py`: missing `REDIS_URL` causes `MemoryStorage` fallback.

FSM-only meaningful data found:

- founder group context: `group_id`, `group_title`, `group_username`, `group_type`
- founder draft: `nation_name`, `currency_code`
- governance draft: `rule_key`, `proposed_value`, `target_scope`, `target_id`, `current_value`
- market amount wizard context: `nation_id`
- first-trade availability flag

Decision: add `OnboardingDraft(player_id, step_key, payload, updated_at)` and use progressive saves.

## Part C keyboard discipline

Direct markup sends were found in `start.py`, `market.py`, `founder.py`, `nation.py`, `governance.py` and `onboarding_fix.py`.

Known conflict:

`💹 بازار` is a ReplyKeyboard action. Its handler opens an InlineKeyboard but does not first remove the ReplyKeyboard. Founder cancellation has the reverse transition problem in other paths.

Decision: add `app/services/keyboard_state.py` as the only keyboard gateway, persist current keyboard kind, and migrate all handler sends.

## Part D states and intent

States present:

- Onboarding: SET_USERNAME_PLAYER, SELECT_NATION
- Founder: WAITING_GROUP_ADMIN, SET_NATION_NAME, CONFIRM
- Market: WAITING_BUY_AMOUNT, WAITING_SELL_AMOUNT
- Governance: WAITING_VALUE, CONFIRM_PROPOSAL
- Nation: IDLE

`NationStates.IDLE` is not used by a live flow and is a legacy state. Decision: remove after verifying no external reference.

Every live state will get a `StepDefinition` with a Persian expected-input description and validator.

Intent priority:

1. registered system command
2. exact active ReplyKeyboard text
3. current-state validator
4. Persian question/help text
5. ambiguous

Expected behavior: never silent, never crash, state remains on off-topic input.

## Runtime test evidence

Local environment baseline:

- GitHub read/write connector: available
- direct container clone: unavailable because network DNS cannot resolve GitHub
- aiogram 3.26.0: not installed locally
- CI has Python 3.12, Postgres 16 and installs repository requirements

Therefore no runtime PASS is claimed in this baseline document. The final version must include actual harness output for:

- every active callback producer
- FSM state handlers
- DB assertions
- restart/recovery
- keyboard transitions
- five intent classes
- full E2E journey
- final pytest result

## Decision register

| Item | Decision |
|---|---|
| MemoryStorage fallback | اصلاح: production fail-fast, explicit dev flag only |
| Founder FSM-only data | اصلاح: progressive `OnboardingDraft` save |
| Governance FSM-only draft | اصلاح: progressive draft |
| Keyboard direct writes | اصلاح: central manager |
| `market_chart` | حذف کامل |
| `founder_panel` | حذف دکمه و handler |
| `create_nation` | حذف کامل |
| legacy trade confirmation | حذف کامل |
| `my_nations` placeholder | اصلاح |
| sections placeholders | پیام صادقانه موقت |
| duplicate cancel path | اصلاح |
| `NationStates.IDLE` | حذف کامل after reference check |

## Protected areas

Do not change economic logic in `app/services/economic_engine.py`, `app/services/rules/`, or `app/services/governance_service.py`. No Phase 4 interaction change may alter economy formulas or governance rules.


---

# Final Phase 4 Verification

## Runtime environment

Final verification was executed by GitHub Actions on Python 3.12 with PostgreSQL 16.

The CI test environment uses:
- `ALLOW_MEMORY_FSM_DEV=true`
- PostgreSQL test database
- `pytest -q -s`

Production behavior is different: `REDIS_URL` is required unless the explicit development flag is enabled.

## Final CI result

**Workflow run:** 286  
**Result:** `success`  
**Steps:** compile, migration upgrade, migration downgrade/upgrade, full pytest.

Final pytest result from CI:

```
20 passed, 19 warnings in 3.38s
```

The warnings are existing `datetime.utcnow()` deprecation warnings in temporal/economic code. No Phase 4 test failed.

## Storage guard output

```
STORAGE|PASS|production_requires_redis|explicit_dev_allows_memory
```

This is the direct runtime check for Part B.1.

## E2E output

The required user journey was executed against the real test PostgreSQL database.

```
E2E|PASS|01|/start|welcome sent
E2E|PASS|02|start_game|FSM=SET_USERNAME_PLAYER|keyboard=inline:onboarding
E2E|PASS|03|invalid_username|state_preserved
E2E|PASS|04|username|DB_saved|draft_saved
E2E|PASS|05|join_nation|User+Holding persisted
E2E|PASS|06|founder_start|draft=waiting_group_admin
E2E|PASS|07|group_connect|draft=persistent
E2E|PASS|08|invalid_nation_name|state_preserved
E2E|PASS|09|nation_name|confirm_draft_saved
E2E|PASS|10|restart|FSM_reset_but_draft_detected
E2E|PASS|11|resume_draft|state+payload_recovered
E2E|PASS|12|confirm_founder|Nation+User+Holding persisted|draft_cleared
E2E|PASS|13|market_open|reply_to_inline_transition
E2E|PASS|14|sell_wizard|state=WAITING_SELL_AMOUNT
E2E|PASS|15|invalid_amount|state_preserved
E2E|PASS|16|cancel|inline_to_reply_restored|draft_cleared
E2E|PASS|17|trade|Transaction+balances persisted
E2E|PASS|18|full_journey|all assertions passed
```

This verifies:
- registration
- DB persistence
- founder wizard
- progressive draft save
- FSM restart
- draft recovery
- nation creation
- market entry
- invalid input
- /cancel
- reply/inline keyboard transition
- real transaction persistence

## Intent result

All five required intent types passed:

```
INTENT|system_command|expected=system_command|actual=system_command
INTENT|state_input|expected=state_input|actual=state_input
INTENT|navigation_text|expected=navigation_text|actual=navigation_text
INTENT|off_topic|expected=off_topic|actual=off_topic
INTENT|ambiguous|expected=ambiguous|actual=ambiguous
```

## Restart result

```
RESTART|PASS|fsm_lost|draft_present
RESTART|PASS|state_recovered_from_db
```

This confirms that a new `MemoryStorage` loses the FSM state while the DB draft remains available and can restore the wizard.

## Migration result

CI successfully executed:
1. `0003 -> 0004`
2. `0004 -> 0003`
3. `0003 -> 0004`

No migration error occurred.

## Keyboard architecture result

A final source scan found **no direct `reply_markup=` send/edit in the seven handler files**:

- `app/handlers/start.py`
- `app/handlers/onboarding_fix.py`
- `app/handlers/founder.py`
- `app/handlers/market.py`
- `app/handlers/nation.py`
- `app/handlers/sections.py`
- `app/handlers/governance.py`

Keyboard writes are routed through `app/services/keyboard_state.py`.

The E2E test verified:
- Reply -> Inline transition when entering market.
- Inline -> Reply transition after cancel.
- Inline wizard state persisted in `KeyboardState`.

## Callback surface result

All callback producers left in `app/keyboards/inline.py` have a live owner in the handlers/services.

Removed dead producers:
- `confirm_trade`
- `cancel_trade`
- `founder_panel`

Removed dead handler:
- `market_chart`

Removed unused FSM state:
- `NationStates.IDLE`

Recovery callbacks (`confirm_restart`, `keep_wizard`, `resume_draft`, `discard_draft`) are owned by `app/services/intent_router.py`.

Dynamic callback families are owned by:
- `join_nation:`
- `buy_`
- `buyq_`
- `cbuy_`
- `sell_`
- `sellq_`
- `csell_`
- `gov_rule:`
- `gov_proposal:`
- `gov_vote:`
- `gov_history:`
- `gov_revoke:`

## Final decision status

| Item | Final decision | Final status |
|---|---|---|
| MemoryStorage production fallback | fail-fast unless explicit dev flag | ✅ |
| Founder FSM-only data | progressive `OnboardingDraft` | ✅ |
| Governance wizard draft | progressive `OnboardingDraft` | ✅ |
| Market wizard context | persistent draft | ✅ |
| Keyboard conflict | central manager | ✅ |
| Intent classification | central middleware | ✅ |
| Dead legacy callbacks | removed | ✅ |
| Placeholder sections | honest temporary message | ✅ |
| Duplicate cancel path | central cancel path | ✅ |
| Nation legacy state | removed | ✅ |

## Protected areas

GitHub compare against `main` shows no Phase 4 change in:
- `app/services/economic_engine.py`
- `app/services/rules/`
- `shadow`
- `blackswan`
- `ruin`

No Phase 4 change was made to economic formulas or the existing governance service logic.
