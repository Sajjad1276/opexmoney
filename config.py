from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    owner_id: int | None = None

    class Config:
        env_file = '.env'


settings = Settings()
