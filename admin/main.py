from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from hmac import compare_digest
from pathlib import Path
import os

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database.session import async_session
from app.bot_webhook import (
    build_webhook_runtime,
    close_webhook_runtime,
    configure_webhook_bot_menu,
)
from admin.routers import (
    actions,
    economy,
    governance,
    nations,
    players,
    stats,
    transactions,
    wars,
    control,
    panel_sections,
)
from config import settings
from app.core.redis import create_redis_client, ServerlessRedis

logger = logging.getLogger("opex.admin")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_ok = False
    redis_ok = False

    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
        logger.info("ADMIN_STARTUP|database=ok")
    except Exception:
        logger.exception("ADMIN_STARTUP|database=failed")

    try:
        redis = create_redis_client()
        if redis is not None:
            await redis.ping()
            app.state.redis = redis
            redis_ok = True
            logger.info("ADMIN_STARTUP|redis=ok")
        else:
            app.state.redis = None
            logger.warning("ADMIN_STARTUP|redis=not_configured")
    except Exception:
        app.state.redis = None
        logger.exception("ADMIN_STARTUP|redis=failed")

    if (
        db_ok
        and redis_ok
        and settings.bot_token
        and os.getenv("VERCEL_ENV") == "production"
    ):
        try:
            bot = Bot(
                token=settings.bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            try:
                webhook_url = (
                    settings.telegram_webhook_url
                    or os.getenv("VERCEL_PROJECT_PRODUCTION_URL")
                    or os.getenv("VERCEL_URL")
                )
                if webhook_url:
                    if not webhook_url.startswith(("http://", "https://")):
                        webhook_url = f"https://{webhook_url}"
                    webhook_url = webhook_url.rstrip("/") + "/api/telegram/webhook"
                    await bot.set_webhook(
                        url=webhook_url,
                        secret_token=settings.telegram_webhook_secret,
                        allowed_updates=[],
                        drop_pending_updates=False,
                    )
                    logger.info("ADMIN_STARTUP|telegram_webhook=%s", webhook_url)
            finally:
                await bot.session.close()
        except Exception:
            logger.exception("ADMIN_STARTUP|telegram_webhook=failed")

    app.state.db_ok = db_ok
    app.state.redis_ok = redis_ok

    if not db_ok:
        logger.error("ADMIN_STARTUP|database_connection_failed")

    try:
        yield
    finally:
        redis = getattr(app.state, "redis", None)
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                logger.exception("ADMIN_SHUTDOWN|redis_close_failed")
        logger.info("ADMIN_SHUTDOWN|complete")


app = FastAPI(
    title="OPEX MONEY Admin API",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^https://([a-z0-9-]+\.)*(vercel\.app|up\.railway\.app)$"
        r"|^http://localhost:3000$"
    ),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "ADMIN_REQUEST|method=%s|path=%s|status=%s|duration_ms=%.2f",
            request.method,
            request.url.path,
            status,
            duration_ms,
        )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "ADMIN_EXCEPTION|method=%s|path=%s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "ADMIN_VALIDATION|method=%s|path=%s|errors=%s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


def _panel_redirect() -> RedirectResponse:
    response = RedirectResponse(
        url="/static/index.html?v=20260923-vercel",
        status_code=307,
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/", include_in_schema=False)
async def root():
    return _panel_redirect()


@app.get("/admin", include_in_schema=False)
async def admin_root():
    return _panel_redirect()


@app.middleware("http")
async def static_cache_control(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/static/index.html":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/health")
async def health(request: Request):
    db_ok = False
    redis_ok = False

    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("ADMIN_HEALTH|database_failed")

    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.ping()
            redis_ok = True
        except Exception:
            logger.exception("ADMIN_HEALTH|redis_failed")

    return {
        "status": "ok",
        "db": db_ok,
        "redis": redis_ok,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _require_setup_token(request: Request) -> None:
    expected = settings.telegram_webhook_setup_token
    received = request.headers.get("X-Telegram-Webhook-Setup-Token", "")
    if not expected or not received or not compare_digest(received, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/api/telegram/setup")
async def telegram_setup(request: Request):
    _require_setup_token(request)

    if not settings.bot_token:
        raise HTTPException(status_code=500, detail="BOT_TOKEN is not configured")
    if create_redis_client() is None:
        raise HTTPException(status_code=500, detail="Redis credentials are not configured")
    if not settings.telegram_webhook_secret:
        raise HTTPException(
            status_code=500,
            detail="TELEGRAM_WEBHOOK_SECRET is not configured",
        )

    webhook_url = settings.telegram_webhook_url
    if not webhook_url:
        webhook_url = str(request.base_url).rstrip("/") + "/api/telegram/webhook"

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=[],
            drop_pending_updates=False,
        )
        await configure_webhook_bot_menu(bot)
        info = await bot.get_webhook_info()
        return {
            "ok": True,
            "url": info.url,
            "pending_update_count": info.pending_update_count,
            "last_error_message": info.last_error_message,
            "max_connections": info.max_connections,
        }
    finally:
        await bot.session.close()


@app.get("/api/telegram/status")
async def telegram_status(request: Request):
    _require_setup_token(request)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        info = await bot.get_webhook_info()
        me = await bot.get_me()
        return {
            "ok": True,
            "bot": {
                "id": me.id,
                "username": me.username,
                "name": me.full_name,
            },
            "webhook": {
                "url": info.url,
                "pending_update_count": info.pending_update_count,
                "last_error_date": info.last_error_date,
                "last_error_message": info.last_error_message,
                "max_connections": info.max_connections,
            },
        }
    finally:
        await bot.session.close()


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    expected = settings.telegram_webhook_secret
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")

    if not expected or not received or not compare_digest(received, expected):
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()
    bot = None
    dp = None
    storage = None
    ranking_redis = None

    try:
        bot, dp, storage, ranking_redis = build_webhook_runtime()
        update = Update.model_validate(body, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception:
        logger.exception("TELEGRAM_WEBHOOK|update_processing_failed")
        raise
    finally:
        if bot is not None and storage is not None:
            await close_webhook_runtime(
                bot=bot,
                storage=storage,
                ranking_redis=ranking_redis,
            )


app.include_router(stats.router)
app.include_router(players.router)
app.include_router(nations.router)
app.include_router(economy.router)
app.include_router(wars.router)
app.include_router(control.router)
app.include_router(panel_sections.router)
app.include_router(governance.router)
app.include_router(transactions.router)
app.include_router(actions.router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
