from decimal import Decimal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    redis_url: str | None = None
    owner_id: int | None = None
    owner_ai_enabled: bool = True
    railway_api_token: str | None = None

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

    # Nation founding eligibility. Kept configurable so game design can change
    # without touching persistence or handlers.
    nation_creation_trade_threshold: Decimal = Decimal("500")
    nation_creation_cost: Decimal = Decimal("500")
    bot_username: str = "OpexMoney_bot"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        return self.database_url


settings = Settings()
