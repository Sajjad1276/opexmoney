# OPEX MONEY Final Structure

opexmoney/
├── app/
│   ├── handlers/
│   │   ├── start.py
│   │   ├── onboarding_fix.py
│   │   ├── nation.py
│   │   ├── market.py
│   │   ├── founder.py
│   │   ├── governance.py
│   │   ├── academy.py
│   │   ├── chart.py
│   │   ├── membership.py
│   │   ├── missions.py
│   │   ├── portfolio.py
│   │   ├── ranking.py
│   │   ├── settings.py
│   │   ├── nation_management.py
│   │   ├── onboarding.py
│   │   ├── start_flow.py
│   │   ├── sections.py
│   │   ├── support.py
│   │   ├── treasury.py
│   │   └── admin_panel.py
│   ├── services/
│   │   ├── economic_engine.py
│   │   ├── governance_service.py
│   │   ├── nation_service.py
│   │   ├── user_service.py
│   │   ├── temporal_service.py
│   │   ├── membership_service.py
│   │   ├── war_service.py
│   │   └── ai_world.py
│   ├── database/
│   │   ├── models.py
│   │   └── session.py
│   ├── keyboards/
│   ├── states/
│   ├── repositories/
│   ├── schedulers/
│   ├── diagnostics/
│   ├── utils/
│   └── ai/
│       └── smart_messages.py
├── admin/
│   ├── main.py
│   ├── auth.py
│   ├── dependencies.py
│   ├── cache.py
│   ├── verify.py
│   ├── routers/
│   │   ├── stats.py
│   │   ├── players.py
│   │   ├── nations.py
│   │   ├── economy.py
│   │   ├── wars.py
│   │   ├── governance.py
│   │   ├── transactions.py
│   │   └── actions.py
│   ├── schemas/
│   │   └── responses.py
│   └── static/
│       └── index.html
├── main.py
├── railway.toml
├── nixpacks.toml
├── Procfile
├── requirements.txt
├── .env.example
└── STRUCTURE.md

## Railway services

The repository is shared by two Railway services:

- opexmoney: Telegram bot, start command python main.py
- admin: FastAPI Mini App, start command uvicorn admin.main:app --host 0.0.0.0 --port $PORT --workers 1 --loop uvloop --http httptools

Both use the same code repository and database/Redis environment. The admin service additionally requires ADMIN_USER_IDS, MINI_APP_URL, and DEV_MODE=false.

railway.toml is kept as the admin service service-level config. Railway config-as-code applies to a single deployment/service, so the bot and admin start commands are configured independently on their respective Railway services.