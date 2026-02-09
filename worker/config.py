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

ENABLE_FORENSICS = env_bool("ENABLE_FORENSICS", "1")
ENABLE_ATTENTION = env_bool("ENABLE_ATTENTION", "1")
ENABLE_EXECUTION = env_bool("ENABLE_EXECUTION", "0")
ENABLE_RISK_VETO = env_bool("ENABLE_RISK_VETO", "0")
ENABLE_ATTENTION_BONUS = env_bool("ENABLE_ATTENTION_BONUS", "0")
ENABLE_PUMPORTAL = env_bool("ENABLE_PUMPORTAL", "0")
ENABLE_ATTENTION_CANDIDATE = env_bool("ENABLE_ATTENTION_CANDIDATE", "1")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_CANDIDATE_WEBHOOK = os.getenv("DISCORD_CANDIDATE_WEBHOOK", "").strip()

HELIUS_WS_URL = os.getenv("HELIUS_WS_URL", "").strip()
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "").strip()

DEX_REFRESH_SECONDS = int(os.getenv("DEX_REFRESH_SECONDS", "10"))
PROMOTE_MIN_CONFIDENCE = float(os.getenv("PROMOTE_MIN_CONFIDENCE", "0.65"))

RISK_VETO_THRESHOLD = float(os.getenv("RISK_VETO_THRESHOLD", "0.80"))
ATTENTION_BONUS_CAP = float(os.getenv("ATTENTION_BONUS_CAP", "0.15"))
ATTENTION_MIN_FOR_WINDOW = float(os.getenv("ATTENTION_MIN_FOR_WINDOW", "0.75"))
ATTENTION_WINDOW_MINUTES = int(os.getenv("ATTENTION_WINDOW_MINUTES", "60"))
ATTENTION_CANDIDATE_THRESHOLD = float(os.getenv("ATTENTION_CANDIDATE_THRESHOLD", "0.70"))
EXECUTION_BONUS_CAP = float(os.getenv("EXECUTION_BONUS_CAP", "0.05"))
MIN_EDGE_BPS = float(os.getenv("MIN_EDGE_BPS", "80"))

# Dedupe / cooldown
EARLY_DEDUPE_TTL_SEC = int(os.getenv("EARLY_DEDUPE_TTL_SEC", "600"))
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", "120"))
