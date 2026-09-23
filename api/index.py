"""Vercel entrypoint for the OPEX MONEY FastAPI application.

The long-running Telegram worker remains a separate process. Vercel hosts the
request/response side of OPEX MONEY, including the admin Mini App and API.
"""

from admin.main import app

__all__ = ["app"]
