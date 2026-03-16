from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: str = "prod"
    ALERT_COOLDOWN_MIN: int = 1440  # 24h per token
    WATCH_LOG_PATH: str = "/data/watch.log"


settings = Settings()
