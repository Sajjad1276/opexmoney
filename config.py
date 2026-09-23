from decimal import Decimal

from pydantic import AliasChoices, Field, model_validator

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str = Field(validation_alias=AliasChoices("DATABASE_URL", "POSTGRES_URL"))
    redis_url: str | None = None
    upstash_redis_rest_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL"),
    )
    upstash_redis_rest_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_TOKEN"),
    )
    owner_id: int | None = None
    owner_ai_enabled: bool = True
    railway_api_token: str | None = None

    telegram_webhook_url: str | None = None
    telegram_webhook_secret: str | None = None
    telegram_webhook_setup_token: str | None = None

    ai_enabled: bool = True
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    ai_model: str = "gemini-3.1-flash-lite"
    ai_timeout_seconds: float = 12.0
    owner_ai_timeout_seconds: float = 120.0
    ai_cache_ttl_seconds: int = 120
    ai_last_message_ttl_seconds: int = 1800
    support_ai_enabled: bool = True
    support_engineering_enabled: bool = True
    support_github_token: str | None = None
    github_token: str | None = None
    support_github_repo: str = "Sajjad1276/opexmoney"
    support_engineering_timeout_seconds: int = 420
    support_max_report_chars: int = 1200
    support_telemetry_ttl_seconds: int = 3600

    governance_proposal_top_percent: Decimal = Decimal("10")
    governance_voting_hours: int = 24
    governance_implementation_days: int = 7
    governance_draft_window_days: int = 7
    governance_min_quorum_weight: Decimal = Decimal("3")
    governance_max_active_rules: int = 5
    governance_circuit_breaker_change_ratio: Decimal = Decimal("0.40")
    governance_circuit_breaker_suspend_hours: int = 1
    governance_voter_activity_days: int = 7

    temporal_peak_window_hours: int = 2
    temporal_peak_multiplier: Decimal = Decimal("1.5")
    temporal_rotation_days: int = 7
    temporal_timezone: str = "Asia/Tehran"

    behavior_snapshot_retention_days: int = 30
    rate_min: Decimal = Decimal("0.10")
    rate_max: Decimal = Decimal("50.00")
    rate_base_step: Decimal = Decimal("0.04")

    nation_creation_trade_threshold: Decimal = Decimal("500")
    nation_creation_cost: Decimal = Decimal("500")
    bot_username: str = "OpexMoney_bot"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def normalize_vercel_integrations(self):
        if not self.redis_url:
            rest_url = self.upstash_redis_rest_url
            token = self.upstash_redis_rest_token
            if rest_url and token:
                from urllib.parse import quote, urlsplit

                parsed = urlsplit(rest_url if "://" in rest_url else f"https://{rest_url}")
                if parsed.hostname:
                    self.redis_url = (
                        f"rediss://default:{quote(token, safe='')}@"
                        f"{parsed.hostname}:6379"
                    )

        if not self.telegram_webhook_secret:
            import hashlib

            self.telegram_webhook_secret = hashlib.sha256(
                f"{self.bot_token}:opex:webhook".encode()
            ).hexdigest()

        if not self.telegram_webhook_setup_token:
            import hashlib

            self.telegram_webhook_setup_token = hashlib.sha256(
                f"{self.bot_token}:opex:setup".encode()
            ).hexdigest()

        return self

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        return self.database_url


settings = Settings()
