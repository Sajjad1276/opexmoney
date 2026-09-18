# Database migrations

OPEX MONEY does not modify database schema during bot startup.

All schema changes must be applied explicitly with Alembic before the bot is started.

## New database

1. Configure DATABASE_URL.
2. Run:
   alembic upgrade head
3. Start the bot.

## Existing database

The previous startup code performed CREATE TABLE, ALTER TABLE, type conversions and a data backfill.

That behavior is removed.

Do not run alembic upgrade head blindly against an existing production database.

Verify that the existing database already matches 0001_initial_schema and 0002_founder_constraints. After verification, mark it at the current head with:

alembic stamp 0002_founder_constraints

If anything is missing, create a forward migration for that difference. Do not restore schema-changing SQL to bot startup.

The old migrations/0001_founder.sql was not an Alembic migration and is retired.
