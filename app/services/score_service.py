from datetime import datetime, timezone
import time


WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
EXCLUDED_QUOTES = {WSOL_MINT, USDC_MINT, USDT_MINT}
SOLANA_CHAIN_VALUES = {"sol", "solana"}
_DEX_TOKEN_STATE: dict[str, dict[str, float]] = {}


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


def _txn_metrics(pair: dict) -> tuple[int, int, float]:
    txns_m5 = pair.get("txns", {}).get("m5", {}) if isinstance(pair.get("txns"), dict) else {}
    buys5m = int(txns_m5.get("buys") or 0) if isinstance(txns_m5, dict) else 0
    sells5m = int(txns_m5.get("sells") or 0) if isinstance(txns_m5, dict) else 0
    sell_ratio = round(sells5m / max(1, buys5m), 4)
    return buys5m, sells5m, sell_ratio


def _paid_visibility_class(sources: list[str], buys5m: int, sells5m: int, vol5m: float) -> str:
    source_set = set(sources)
    paid = bool({"paid_ad", "token_boost_latest", "token_boost_top", "dex_boost_active"} & source_set)
    has_cto = "community_takeover" in source_set
    has_profile = "token_profile" in source_set
    has_flow = buys5m >= 8 and vol5m >= 5_000 and sells5m <= buys5m * 2
    if not paid:
        return "organic"
    if has_flow and has_cto:
        return "paid_plus_cto_flow"
    if has_flow:
        return "paid_plus_flow"
    if has_cto:
        return "paid_plus_cto"
    if has_profile:
        return "paid_plus_profile"
    return "paid_only"


def _scan_evidence(token: str, *, volume_5m: float, price_change_5m: float, liquidity: float, observed_ts: float) -> dict:
    prior = _DEX_TOKEN_STATE.get(token) or {}
    first_seen = float(prior.get("first_seen_ts") or observed_ts)
    previous_seen = float(prior.get("last_seen_ts") or observed_ts)
    previous_volume = float(prior.get("volume_5m") or 0.0)
    previous_liquidity = float(prior.get("liquidity") or 0.0)
    repeat_count = int(prior.get("repeat_count") or 0) + 1
    minutes_since_prev = max((observed_ts - previous_seen) / 60.0, 0.0)
    volume_delta = volume_5m - previous_volume if previous_volume else 0.0
    liquidity_delta_pct = ((liquidity - previous_liquidity) / previous_liquidity * 100.0) if previous_liquidity else 0.0
    _DEX_TOKEN_STATE[token] = {
        "first_seen_ts": first_seen,
        "last_seen_ts": observed_ts,
        "volume_5m": volume_5m,
        "liquidity": liquidity,
        "price_change_5m": price_change_5m,
        "repeat_count": float(repeat_count),
    }
    return {
        "dex_scan_first_seen_age_seconds": round(observed_ts - first_seen, 1),
        "dex_scan_repeat_count": repeat_count,
        "dex_scan_minutes_since_previous": round(minutes_since_prev, 2) if repeat_count > 1 else None,
        "dex_scan_volume_delta_5m": round(volume_delta, 2),
        "dex_scan_liquidity_delta_pct": round(liquidity_delta_pct, 4),
        "dex_scan_price_change_5m": round(price_change_5m, 2),
        "dex_scan_momentum_slope": round(volume_delta / max(minutes_since_prev, 1.0), 4) if repeat_count > 1 else 0.0,
        "dex_scan_persistent": repeat_count >= 2,
    }


def score_pairs(pairs: list[dict]) -> list[dict]:
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    now_ts = time.time()
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
            buys5m, sells5m, sell_ratio = _txn_metrics(p)
            market_cap = float(p.get("marketCap") or p.get("fdv") or 0)
            sources = _discovery_sources(p)
            has_curated_source = bool(
                {
                    "community_takeover",
                    "external_seed",
                    "j7tracker",
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
                scan_evidence = _scan_evidence(
                    token,
                    volume_5m=vol5m,
                    price_change_5m=chg5m,
                    liquidity=liq,
                    observed_ts=now_ts,
                )
                paid_class = _paid_visibility_class(sources, buys5m, sells5m, vol5m)
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
                            "sells_5m": sells5m,
                            "sell_ratio_5m": sell_ratio,
                            "discovery_sources": sources,
                            "community_takeover": "community_takeover" in sources,
                            "source_stability": "repeat_seen" if scan_evidence["dex_scan_persistent"] else "first_seen",
                            "paid_visibility_class": paid_class,
                            "independent_flow_confirmed": bool(buys5m >= 8 and vol5m >= 5_000 and sells5m <= buys5m * 2),
                            "paid_visibility": bool(
                                {
                                    "paid_ad",
                                    "token_boost_latest",
                                    "token_boost_top",
                                    "dex_boost_active",
                                }
                                & set(sources)
                            ),
                            **scan_evidence,
                        },
                    }
                )

        except Exception:
            continue

    return out
