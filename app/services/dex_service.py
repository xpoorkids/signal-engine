import requests

DEX_URL = "https://api.dexscreener.com/latest/dex/search?q=solana"


def fetch_solana_pairs():
    r = requests.get(DEX_URL, timeout=10)
    r.raise_for_status()
    pairs = r.json().get("pairs", [])
    print(f"[dex] fetched {len(pairs)} pairs", flush=True)
    return pairs
