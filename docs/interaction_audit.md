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