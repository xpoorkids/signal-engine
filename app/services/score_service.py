from datetime import datetime, timezone


WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
EXCLUDED_QUOTES = {WSOL_MINT, USDC_MINT, USDT_MINT}
SOLANA_CHAIN_VALUES = {"sol", "solana"}


def _clean_address(value: object) -> str:
    return str(value or "").strip()


def _clean_chain(value: object) -> str:
    return str(value or "").strip().lower()


def _is_solana_pair(pair: dict) -> bool:
    chain_id = _clean_chain(pair.get("chainId"))
    if chain_id:
        return chain_id in SOLANA_CHAIN_VALUES

    chain = _clean_chain(pair.get("chain"))
    if chain:
        return chain in SOLANA_CHAIN_VALUES

    return False


def _pick_contract_address(pair: dict) -> str | None:
    base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    quote = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}

    base_addr = _clean_address(base.get("address"))
    quote_addr = _clean_address(quote.get("address"))

    if base_addr and quote_addr:
        if base_addr in EXCLUDED_QUOTES and quote_addr not in EXCLUDED_QUOTES:
            return quote_addr
        if quote_addr in EXCLUDED_QUOTES and base_addr not in EXCLUDED_QUOTES:
            return base_addr
        if base_addr == quote_addr:
            return base_addr
        if base_addr.endswith("pump") and not quote_addr.endswith("pump"):
            return base_addr
        if quote_addr.endswith("pump") and not base_addr.endswith("pump"):
            return quote_addr

    if base_addr and base_addr not in EXCLUDED_QUOTES:
        return base_addr
    if quote_addr and quote_addr not in EXCLUDED_QUOTES:
        return quote_addr
    return base_addr or quote_addr or None


def _pick_symbol(pair: dict, token_address: str | None) -> str | None:
    base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    quote = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}

    if token_address:
        if str(base.get("address") or "").strip() == token_address:
            return base.get("symbol")
        if str(quote.get("address") or "").strip() == token_address:
            return quote.get("symbol")

    return base.get("symbol") or quote.get("symbol")


def score_pairs(pairs: list[dict]) -> list[dict]:
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    out = []

    for p in pairs:
        try:
            if not _is_solana_pair(p):
                continue

            liq = float(p.get("liquidity", {}).get("usd") or 0)
            vol5m = float(p.get("volume", {}).get("m5") or 0)
            chg5m = float(p.get("priceChange", {}).get("m5") or 0)
            created = p.get("pairCreatedAt")
            if not created:
                continue

            age = (now_ms - created) / 60000

            if age <= 0.5 and liq >= 800 and vol5m >= 20 and chg5m >= -10:
                token = _pick_contract_address(p)
                if not token:
                    continue
                out.append(
                    {
                        "token": token,
                        "symbol": _pick_symbol(p, token),
                        "chain": "sol",
                        "reason": "aggressive_near_pass",
                        "metrics": {
                            "liquidity": round(liq, 2),
                            "volume_5m": round(vol5m, 2),
                            "price_change_5m": round(chg5m, 2),
                            "age_minutes": round(age, 1),
                        },
                    }
                )

        except Exception:
            continue

    return out
