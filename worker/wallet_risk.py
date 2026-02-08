import os
import requests


RUGCHECK_BASE = os.getenv("RUGCHECK_BASE", "https://api.rugcheck.xyz").rstrip("/")


async def score_wallet_risk(wallet: str) -> dict:
    result = {
        "wallet": wallet,
        "score": 0.0,
        "flags": [],
    }

    try:
        r = requests.get(f"{RUGCHECK_BASE}/v1/wallet/{wallet}", timeout=6)
        if r.status_code == 200:
            data = r.json()
            risk = float(data.get("risk", 0.0)) if isinstance(data, dict) else 0.0
            result["score"] = min(1.0, max(result["score"], risk))
            if risk >= 0.7:
                result["flags"].append("rugcheck_high_risk")
    except Exception:
        pass

    if wallet.lower().startswith("1111"):
        result["flags"].append("odd_wallet_prefix")

    return result
