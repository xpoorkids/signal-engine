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


def _discovery_sources(pair: dict) -> list[str]:
    raw = pair.get("signal_engine_sources")
    sources: list[str] = []
    if isinstance(raw, list):
        sources.extend(str(item).strip() for item in raw if str(item or "").strip())
    source = str(pair.get("signal_engine_source") or "").strip()
    if source:
        sources.append(source)
    boosts = pair.get("boosts") if isinstance(pair.get("boosts"), dict) else {}
    try:
        if float(boosts.get("active") or boosts.get("amount") or 0) > 0:
            sources.append("dex_boost_active")
    except Exception:
        pass
    return list(dict.fromkeys(sources))


def score_pairs(pairs: list[dict]) -> list[dict]:
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    out = []
    seen_tokens: set[str] = set()

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
            txns_m5 = p.get("txns", {}).get("m5", {}) if isinstance(p.get("txns"), dict) else {}
            buys5m = int(txns_m5.get("buys") or 0) if isinstance(txns_m5, dict) else 0
            market_cap = float(p.get("marketCap") or p.get("fdv") or 0)
            sources = _discovery_sources(p)
            has_curated_source = bool(
                {
                    "community_takeover",
                    "external_seed",
                    "token_profile",
                    "token_boost_top",
                }
                & set(sources)
            )
            near_pass = age <= 0.5 and liq >= 800 and vol5m >= 20 and chg5m >= -10
            momentum_watch = (
                age <= 24 * 60
                and liq >= 10_000
                and vol5m >= 1_000
                and buys5m >= 3
                and chg5m >= -18
                and 25_000 <= market_cap <= 5_000_000
            )
            discovery_watch = (
                has_curated_source
                and age <= 7 * 24 * 60
                and liq >= 25_000
                and vol5m >= 500
                and chg5m >= -20
                and 50_000 <= market_cap <= 50_000_000
            )

            if near_pass or momentum_watch or discovery_watch:
                token = _pick_contract_address(p)
                if not token or token in seen_tokens:
                    continue
                seen_tokens.add(token)
                reason = "aggressive_near_pass" if near_pass else "dex_momentum_watch"
                if discovery_watch and not near_pass and not momentum_watch:
                    reason = "curated_discovery_watch"
                out.append(
                    {
                        "token": token,
                        "symbol": _pick_symbol(p, token),
                        "chain": "sol",
                        "reason": reason,
                        "metrics": {
                            "liquidity": round(liq, 2),
                            "volume_5m": round(vol5m, 2),
                            "price_change_5m": round(chg5m, 2),
                            "age_minutes": round(age, 1),
                            "market_cap": round(market_cap, 2),
                            "buys_5m": buys5m,
                            "discovery_sources": sources,
                            "community_takeover": "community_takeover" in sources,
                            "paid_visibility": bool(
                                {
                                    "paid_ad",
                                    "token_boost_latest",
                                    "token_boost_top",
                                    "dex_boost_active",
                                }
                                & set(sources)
                            ),
                        },
                    }
                )

        except Exception:
            continue

    return out
