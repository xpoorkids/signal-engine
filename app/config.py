from pydantic import BaseSettings

class Settings(BaseSettings):
    ENV: str = "prod"
    ALERT_COOLDOWN_MIN: int = 1440  # 24h per token

settings = Settings()
