from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent.parent
OK = "✅"
WARN = "⚠️"
FAIL = "❌"


def report(symbol: str, name: str, message: str) -> None:
    print(f"{symbol} {name} — {message}")


async def wait_port(host: str, port: int, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            await asyncio.sleep(0.25)
    return False


async def main() -> int:
    failures = 0

    try:
        from admin.main import app
        from fastapi import FastAPI

        if not isinstance(app, FastAPI):
            raise TypeError("admin.main.app is not a FastAPI instance")
        report(OK, "admin.main import", "FastAPI app موجود است")
    except Exception as exc:
        report(FAIL, "admin.main import", str(exc))
        return 1

    try:
        from admin.auth import settings

        report(OK, "admin.auth settings", "تنظیمات بدون خطا بارگذاری شد")
    except Exception as exc:
        report(FAIL, "admin.auth settings", str(exc))
        failures += 1
        settings = None

    if settings is not None:
        if settings.BOT_TOKEN.strip():
            report(OK, "BOT_TOKEN", "مقدار خالی نیست")
        else:
            report(FAIL, "BOT_TOKEN", "خالی است")
            failures += 1

        try:
            admin_ids = settings.admin_ids
        except Exception as exc:
            report(FAIL, "ADMIN_USER_IDS", str(exc))
            failures += 1
        else:
            if admin_ids:
                report(OK, "ADMIN_USER_IDS", f"{len(admin_ids)} ادمین ثبت شده")
            else:
                report(FAIL, "ADMIN_USER_IDS", "لیست ادمین‌ها خالی است")
                failures += 1

    mini_url = os.getenv("MINI_APP_URL", "").strip()
    if not mini_url:
        report(FAIL, "MINI_APP_URL", "خالی است")
        failures += 1
    elif mini_url.startswith("http://localhost") or mini_url.startswith("https://localhost"):
        report(WARN, "MINI_APP_URL", "localhost است و برای production مناسب نیست")
    elif mini_url.startswith("https://"):
        report(OK, "MINI_APP_URL", mini_url)
    else:
        report(FAIL, "MINI_APP_URL", "باید با https:// شروع شود")
        failures += 1

    try:
        from app.database.session import async_session
        from sqlalchemy import text

        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        report(OK, "database", "SELECT 1 موفق بود")
    except Exception as exc:
        report(FAIL, "database", str(exc))
        failures += 1

    try:
        from redis import asyncio as aioredis

        redis_url = os.getenv("REDIS_URL", "").strip()
        if not redis_url:
            raise RuntimeError("REDIS_URL خالی است")
        redis = aioredis.from_url(redis_url)
        try:
            await redis.ping()
        finally:
            await redis.aclose()
        report(OK, "redis", "PING موفق بود")
    except Exception as exc:
        report(FAIL, "redis", str(exc))
        failures += 1

    route_paths = {getattr(route, "path", "") for route in app.routes}
    required = [
        "/api/stats/overview",
        "/api/players",
        "/api/nations",
        "/api/economy/market-states",
        "/api/wars",
        "/api/governance/proposals",
        "/api/transactions",
        "/api/admin/actions/broadcast",
        "/api/admin/actions/audit-log",
    ]
    missing = [path for path in required if path not in route_paths]
    if missing:
        report(FAIL, "router registration", f"مسیرهای مفقود: {missing}")
        failures += 1
    else:
        report(OK, "router registration", "همه مسیرهای اصلی ثبت شده‌اند")

    index_path = ROOT / "admin" / "static" / "index.html"
    if index_path.exists() and index_path.stat().st_size > 10_000:
        report(OK, "static dashboard", f"index.html = {index_path.stat().st_size} bytes")
    else:
        report(FAIL, "static dashboard", "index.html وجود ندارد یا کمتر از 10KB است")
        failures += 1

    port = 18765
    process = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "admin.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not await wait_port("127.0.0.1", port):
            raise RuntimeError("سرور محلی روی پورت تست بالا نیامد")

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"http://127.0.0.1:{port}/health")

        if response.status_code == 200:
            body = response.json()
            report(OK, "health HTTP", f"HTTP 200 · db={body.get('db')} redis={body.get('redis')}")
        else:
            report(FAIL, "health HTTP", f"HTTP {response.status_code}")
            failures += 1
    except Exception as exc:
        report(FAIL, "health HTTP", str(exc))
        failures += 1
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    if failures:
        print(f"Verification failed: {failures} check(s).")
        return 1

    print("All verification checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
