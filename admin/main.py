from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database.session import async_session
from admin.dependencies import get_redis
from admin.routers import (
    actions,
    economy,
    governance,
    nations,
    players,
    stats,
    transactions,
    wars,
)

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
        from redis import asyncio as aioredis
        from config import settings

        if settings.redis_url:
            redis = aioredis.from_url(settings.redis_url, decode_responses=False)
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
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https://.*\\.up\\.railway\\.app$|^http://localhost:3000$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"] ,
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
        exc_info=exc,
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


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/static/index.html", status_code=307)


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


app.include_router(stats.router)
app.include_router(players.router)
app.include_router(nations.router)
app.include_router(economy.router)
app.include_router(wars.router)
app.include_router(governance.router)
app.include_router(transactions.router)
app.include_router(actions.router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
