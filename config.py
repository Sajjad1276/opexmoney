from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    redis_url: str | None = None
    allow_memory_fsm_dev: bool = False
    owner_id: int | None = None

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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        return self.database_url


settings = Settings()
