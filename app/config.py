class Settings(BaseSettings):
    WATCH_LOG_PATH: str = "/data/watch.log"

    class Config:
        env_file = ".env"

class Settings(BaseSettings):
    ENV: str = "prod"
    ALERT_COOLDOWN_MIN: int = 1440  # 24h per token
    WATCH_LOG_PATH: str = "/data/watch.log"

settings = Settings()
