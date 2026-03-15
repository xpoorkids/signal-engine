import re
from typing import Any

from app.services.wallet_service import wallet_risk_score
from worker.discord import format_discord
from worker.dex import dex_enrich_token, select_best_pair, summarize_pair
from worker.elite import ELITE
from worker.events import Event
from worker.metadata import fetch_token_metadata
from worker.x_signal import fetch_x_signal


_CA_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,48}$")


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _review_attention(dex_summary: dict[str, Any], x_signal: dict[str, Any] | None) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    liq = _float(dex_summary.get("liquidity_usd"))
    vol5 = _float(dex_summary.get("volume_m5"))
    buys5 = _int(dex_summary.get("txns_m5_buys"))
    sells5 = _int(dex_summary.get("txns_m5_sells"))
    age = _float(dex_summary.get("age_minutes"))

    if dex_summary:
        score += 0.20
        reasons.append("dex_pair_found")
    if liq >= 15000:
        score += 0.20
        reasons.append("liquidity_15000_plus")
    elif liq >= 8000:
        score += 0.10
        reasons.append("liquidity_8000_plus")
    if vol5 >= 8000:
        score += 0.15
        reasons.append("volume_m5_8000_plus")
    elif vol5 >= 2000:
        score += 0.08
        reasons.append("volume_m5_2000_plus")
    if buys5 >= 15 and buys5 > sells5:
        score += 0.15
        reasons.append("buy_pressure_m5")
    elif buys5 >= 8:
        score += 0.08
        reasons.append("steady_buys_m5")
    if 0 < age <= 120:
        score += 0.10
        reasons.append("early_age_window")

    if x_signal:
        tweets = _int(x_signal.get("tweet_count"))
        authors = _int(x_signal.get("unique_authors"))
        likes = _int(x_signal.get("likes"))
        if tweets >= 10 and authors >= 10:
            score += 0.20
            reasons.append("x_social_momentum_10_10")
        elif tweets >= 5 and authors >= 5:
            score += 0.10
            reasons.append("x_social_momentum_5_5")
        if likes >= 50:
            score += 0.05
            reasons.append("x_engagement")

    return max(0.0, min(score, 1.0)), reasons


def _review_risk(
    mint_authority: bool,
    freeze_authority: bool,
    wallet_risk: dict[str, Any],
    liq_usd: float,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if mint_authority:
        score += 0.40
        reasons.append("mint_authority_active")
    if freeze_authority:
        score += 0.35
        reasons.append("freeze_authority_active")
    if liq_usd == 0:
        score += 0.10
        reasons.append("liquidity_unknown")
    elif liq_usd < 5000:
        score += 0.15
        reasons.append("thin_liquidity")

    holder_risk = str(wallet_risk.get("risk") or "ok")
    if holder_risk == "high":
        score += 0.25
        reasons.append(str(wallet_risk.get("reason") or "holder_concentration_high"))
    elif holder_risk == "warn":
        score += 0.15
        reasons.append(str(wallet_risk.get("reason") or "holder_concentration_warn"))

    return max(0.0, min(score, 1.0)), reasons


async def review_contract(token: str) -> dict[str, Any]:
    token = (token or "").strip()
    if not _CA_RE.match(token):
        raise ValueError("invalid_solana_contract_address")

    metadata = fetch_token_metadata(token) or {}
    dex_data = await dex_enrich_token(token)
    best_pair = select_best_pair(dex_data, token) if isinstance(dex_data, dict) else None
    dex_summary = summarize_pair(best_pair) if best_pair else {}

    mint_authority, freeze_authority = ELITE.auth_check(token)
    liq_usd, liq_locked, liq_drop = ELITE.liq_check(token, dex_summary if dex_summary else None)
    try:
        holder_risk = wallet_risk_score(token)
    except Exception:
        holder_risk = {
            "enabled": True,
            "top_holder_pct": None,
            "top10_pct": None,
            "risk": "warn",
            "reason": "helius_unavailable",
        }
    x_signal = fetch_x_signal(
        token,
        str(metadata.get("symbol") or ""),
        str(metadata.get("name") or ""),
    )

    attention_score, attention_reasons = _review_attention(dex_summary, x_signal)
    risk_score, risk_reasons = _review_risk(
        mint_authority,
        freeze_authority,
        holder_risk,
        liq_usd,
    )

    lifecycle = "dex" if dex_summary else "bonding_curve"
    elite_score = ELITE.compute_elite_score(
        token=token,
        buy_size_sol=0.0,
        unique_10s=0,
        total_buys_30s=0,
        unique_wallets_30s=0,
        top_wallet_share=0.0,
        liq_usd=liq_usd,
        liq_locked=liq_locked,
        hard_fail=bool(mint_authority or freeze_authority),
    )

    event = Event(
        type="candidate",
        source="review_api",
        token=token,
        confidence=attention_score,
        reasons=attention_reasons + risk_reasons,
        extra={
            "symbol": metadata.get("symbol") or "",
            "name": metadata.get("name") or "",
            "attention_score": attention_score,
            "risk_score": risk_score,
            "attention_reasons": attention_reasons,
            "attention_metrics": {
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
                "narrative_hits": [],
                "x_tweet_count": _int((x_signal or {}).get("tweet_count")),
                "x_unique_authors": _int((x_signal or {}).get("unique_authors")),
                "x_likes": _int((x_signal or {}).get("likes")),
                "unique_buyers_5m": 0,
                "unique_buyers_15m": 0,
            },
            "risk_flags": {
                "holder_concentration": str(holder_risk.get("risk") or "") in ("warn", "high"),
            },
            "risk_reasons": risk_reasons,
            "wallet_risk": holder_risk,
            "elite_score": elite_score,
            "dex_summary": dex_summary,
            "lifecycle": lifecycle,
            "review_mode": True,
        },
    )

    return {
        "token": token,
        "name": metadata.get("name") or metadata.get("symbol") or "UNK",
        "symbol": metadata.get("symbol") or "UNK",
        "lifecycle": lifecycle,
        "attention_score": attention_score,
        "risk_score": risk_score,
        "elite_score": elite_score,
        "attention_reasons": attention_reasons,
        "risk_reasons": risk_reasons,
        "market": {
            "liquidity_usd": dex_summary.get("liquidity_usd"),
            "market_cap": dex_summary.get("market_cap"),
            "fdv": dex_summary.get("fdv"),
            "volume_m5": dex_summary.get("volume_m5"),
            "volume_h1": dex_summary.get("volume_h1"),
            "txns_m5_buys": dex_summary.get("txns_m5_buys"),
            "txns_m5_sells": dex_summary.get("txns_m5_sells"),
            "price_change_m5": dex_summary.get("price_change_m5"),
            "price_change_h1": dex_summary.get("price_change_h1"),
            "price_change_h24": dex_summary.get("price_change_h24"),
            "age_minutes": dex_summary.get("age_minutes"),
        },
        "security": {
            "mint_authority_active": mint_authority,
            "freeze_authority_active": freeze_authority,
            "liquidity_locked": liq_locked,
            "liquidity_drop_spike": liq_drop,
            "holder_risk": holder_risk,
        },
        "social": x_signal or {
            "tweet_count": 0,
            "unique_authors": 0,
            "likes": 0,
            "retweets": 0,
            "replies": 0,
        },
        "links": {
            "dexscreener": f"https://dexscreener.com/solana/{token}",
            "birdeye": f"https://birdeye.so/token/{token}?chain=solana",
            "website_url": dex_summary.get("website_url"),
            "twitter_url": dex_summary.get("twitter_url"),
            "telegram_url": dex_summary.get("telegram_url"),
        },
        "discord_preview": format_discord(event),
    }
