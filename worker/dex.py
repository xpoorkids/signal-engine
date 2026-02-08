import os
import requests


DEX_BASE = os.getenv("DEX_BASE", "https://api.dexscreener.com/latest").rstrip("/")


async def dex_enrich_token(token: str) -> dict:
    try:
        r = requests.get(f"{DEX_BASE}/dex/tokens/{token}", timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {"ok": False}
