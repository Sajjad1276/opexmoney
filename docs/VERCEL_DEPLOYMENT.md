# Vercel deployment

OPEX MONEY is deployed to Vercel as a FastAPI application for request/response workloads.

## What Vercel hosts

- Admin Mini App and admin API
- FastAPI health endpoint
- Any other HTTP routes exposed by `admin.main:app`

The Telegram bot worker and APScheduler are not started by Vercel. They require a long-running worker process.

## Required environment variables

Set these in the Vercel project for Production and Preview as appropriate:

- `BOT_TOKEN`
- `DATABASE_URL`
- `REDIS_URL`
- `ADMIN_USER_IDS`

Optional variables used by the shared application configuration can be added from `.env.example`.

## Database URL

`DATABASE_URL` must be reachable from Vercel and should use a PostgreSQL connection string. The application normalizes `postgresql://` and `postgres://` to the async SQLAlchemy driver automatically.

## First verification

After deployment, verify:

- `/health`
- `/`
- `/static/index.html`
- an authenticated `/api/*` admin endpoint from the Telegram Mini App

The root route redirects to the admin panel and deliberately disables HTML caching.

## Important

Do not run `main.py` on Vercel. That process starts Telegram long polling and APScheduler, which are long-running worker workloads rather than serverless HTTP workloads.
