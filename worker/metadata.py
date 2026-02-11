import os
import requests
from worker.config import HELIUS_API_KEY, HELIUS_RPC_URL

HELIUS_KEY = HELIUS_API_KEY or os.getenv("HELIUS_API_KEY")
HELIUS_RPC = (
    HELIUS_RPC_URL
    or os.getenv("HELIUS_RPC_URL")
    or f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
)


def fetch_token_metadata(mint: str) -> dict | None:
    if not mint or not HELIUS_RPC:
        return None
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAsset",
            "params": {"id": mint},
        }
        r = requests.post(HELIUS_RPC, json=payload, timeout=8)
        if r.status_code >= 300:
            print(f"[metadata] getAsset status={r.status_code} body={r.text[:120]}", flush=True)
            return None
        data = r.json()
        result = data.get("result") or {}
        content = result.get("content") or {}
        metadata = content.get("metadata") or {}
        symbol = (metadata.get("symbol") or "").strip()
        name = (metadata.get("name") or result.get("name") or "").strip()
        if not symbol and not name:
            return None
        return {"symbol": symbol, "name": name}
    except Exception:
        print("[metadata] getAsset exception", flush=True)
        return None
