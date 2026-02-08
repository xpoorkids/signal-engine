import os


def env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "y", "on")


DRY_RUN = env_bool("DRY_RUN", "1")

ENABLE_WS = env_bool("ENABLE_WS", "1")
ENABLE_DEX = env_bool("ENABLE_DEX", "1")
ENABLE_DISCORD = env_bool("ENABLE_DISCORD", "0")
ENABLE_WALLET = env_bool("ENABLE_WALLET", "0")

ENABLE_LOGS_SUB = env_bool("ENABLE_LOGS_SUB", "1")
ENABLE_WS_EARLY = env_bool("ENABLE_WS_EARLY", "1")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

HELIUS_WS_URL = os.getenv("HELIUS_WS_URL", "").strip()
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()

DEX_REFRESH_SECONDS = int(os.getenv("DEX_REFRESH_SECONDS", "10"))
PROMOTE_MIN_CONFIDENCE = float(os.getenv("PROMOTE_MIN_CONFIDENCE", "0.65"))

# Dedupe / cooldown
EARLY_DEDUPE_TTL_SEC = int(os.getenv("EARLY_DEDUPE_TTL_SEC", "600"))
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", "120"))
