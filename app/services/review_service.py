import html
import re
from typing import Any

from app.services.signal_presentation import (
    SignalViewModel,
    confidence_band,
    flow_bias_label,
    format_currency_compact,
    format_percent_compact,
    momentum_label,
    quality_tier,
    risk_band,
    score_band,
    signal_color,
    signal_title,
    signal_type,
    summary_blurb,
)
from app.services.signal_metrics import get_metric_meta, metric_label, metric_state, to_optional_float
from app.services.wallet_service import wallet_risk_score
from worker.discord import format_discord
from worker.dex import dex_enrich_token, select_best_pair, summarize_pair
from worker.elite import ELITE
from worker.events import Event
from worker.metadata import fetch_token_metadata
from worker.x_signal import fetch_x_signal
from app.services.action_engine_service import ActionEngineService, action_engine_enabled


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


def _fmt_num(value: Any, decimals: int = 0, prefix: str = "") -> str:
    try:
        num = float(value)
    except Exception:
        return "--"
    if decimals:
        return f"{prefix}{num:,.{decimals}f}"
    return f"{prefix}{num:,.0f}"


def _fmt_pct(value: Any, decimals: int = 0) -> str:
    try:
        num = float(value)
    except Exception:
        return "--"
    sign = "+" if num > 0 else ""
    if decimals:
        return f"{sign}{num:.{decimals}f}%"
    return f"{sign}{num:.0f}%"


def _review_attention(dex_summary: dict[str, Any], x_signal: dict[str, Any] | None) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    liq = _float(dex_summary.get("liquidity_usd"))
    vol5 = _float(dex_summary.get("volume_m5"))
    vol1h = _float(dex_summary.get("volume_h1"))
    buys5 = _int(dex_summary.get("txns_m5_buys"))
    sells5 = _int(dex_summary.get("txns_m5_sells"))
    age = _float(dex_summary.get("age_minutes"))
    chg1h = _float(dex_summary.get("price_change_h1"))

    buy_sell_ratio = buys5 / max(sells5, 1) if buys5 > 0 else 0.0
    vol_liq_ratio = vol5 / liq if liq > 0 else 0.0

    if dex_summary:
        score += 0.12
        reasons.append("Dex pair is live")

    if liq >= 50000:
        score += 0.24
        reasons.append(f"Strong liquidity base at {_fmt_num(liq, prefix='$')}")
    elif liq >= 15000:
        score += 0.18
        reasons.append(f"Tradable liquidity at {_fmt_num(liq, prefix='$')}")
    elif liq >= 8000:
        score += 0.10
        reasons.append(f"Decent early liquidity at {_fmt_num(liq, prefix='$')}")
    elif liq >= 3000:
        score += 0.05
        reasons.append(f"Starter liquidity at {_fmt_num(liq, prefix='$')}")

    if vol5 >= 50000:
        score += 0.20
        reasons.append(f"Heavy 5m turnover at {_fmt_num(vol5, prefix='$')}")
    elif vol5 >= 15000:
        score += 0.14
        reasons.append(f"Strong 5m turnover at {_fmt_num(vol5, prefix='$')}")
    elif vol5 >= 5000:
        score += 0.08
        reasons.append(f"Healthy 5m turnover at {_fmt_num(vol5, prefix='$')}")

    if vol1h >= 100000:
        score += 0.06
        reasons.append(f"1h participation is holding at {_fmt_num(vol1h, prefix='$')}")

    if buys5 >= 40 and buy_sell_ratio >= 1.4:
        score += 0.18
        reasons.append(f"Order flow is buy-led at {buys5}/{sells5} buys to sells")
    elif buys5 >= 15 and buy_sell_ratio >= 1.1:
        score += 0.12
        reasons.append(f"Buy pressure is positive at {buys5}/{sells5} buys to sells")
    elif buys5 >= 8:
        score += 0.06
        reasons.append(f"Steady trade count with {buys5} buys in 5m")

    if vol_liq_ratio >= 1.5 and liq >= 8000:
        score += 0.08
        reasons.append(f"Turnover is moving at {vol_liq_ratio:.1f}x liquidity in 5m")
    elif vol_liq_ratio >= 0.7 and liq >= 8000:
        score += 0.04
        reasons.append(f"Turnover is active at {vol_liq_ratio:.1f}x liquidity in 5m")

    if 0 < age <= 30:
        score += 0.10
        reasons.append(f"Still early at {age:.1f}m old")
    elif age <= 180:
        score += 0.06
        reasons.append(f"Still developing at {age:.1f}m old")

    if chg1h >= 100:
        score += 0.08
        reasons.append(f"Price expansion is strong at {_fmt_pct(chg1h, 1)} over 1h")
    elif chg1h >= 25:
        score += 0.04
        reasons.append(f"Price trend is constructive at {_fmt_pct(chg1h, 1)} over 1h")

    if x_signal:
        tweets = _int(x_signal.get("tweet_count"))
        authors = _int(x_signal.get("unique_authors"))
        likes = _int(x_signal.get("likes"))

        if tweets >= 20 and authors >= 15:
            score += 0.22
            reasons.append(f"X traction is broad with {tweets} mentions across {authors} authors")
        elif tweets >= 10 and authors >= 10:
            score += 0.16
            reasons.append(f"X traction is real with {tweets} mentions across {authors} authors")
        elif tweets >= 5 and authors >= 5:
            score += 0.08
            reasons.append(f"X chatter is building with {tweets} mentions across {authors} authors")

        if likes >= 100:
            score += 0.06
            reasons.append(f"X engagement is elevated at {likes} likes")
        elif likes >= 25:
            score += 0.03
            reasons.append(f"X engagement is visible at {likes} likes")

    return max(0.0, min(score, 1.0)), reasons


def _review_risk(
    mint_authority: bool,
    freeze_authority: bool,
    wallet_risk: dict[str, Any],
    liq_usd: float,
    dex_summary: dict[str, Any],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    top_holder_pct = _float(wallet_risk.get("top_holder_pct")) * 100.0
    holder_level = str(wallet_risk.get("risk") or "ok")
    vol5 = _float(dex_summary.get("volume_m5"))
    age = _float(dex_summary.get("age_minutes"))

    if mint_authority:
        score += 0.30
        reasons.append("Mint authority is still active")
    if freeze_authority:
        score += 0.25
        reasons.append("Freeze authority is still active")

    if liq_usd == 0:
        score += 0.08
        reasons.append("Liquidity is still unconfirmed")
    elif liq_usd < 2000:
        score += 0.22
        reasons.append(f"Liquidity is very thin at {_fmt_num(liq_usd, prefix='$')}")
    elif liq_usd < 5000:
        score += 0.15
        reasons.append(f"Liquidity is thin at {_fmt_num(liq_usd, prefix='$')}")
    elif liq_usd < 15000:
        score += 0.08
        reasons.append(f"Liquidity is subscale at {_fmt_num(liq_usd, prefix='$')}")

    if top_holder_pct >= 20:
        score += 0.30
        reasons.append(f"Top holder concentration is severe at {top_holder_pct:.1f}%")
    elif top_holder_pct >= 15:
        score += 0.22
        reasons.append(f"Top holder concentration is high at {top_holder_pct:.1f}%")
    elif top_holder_pct >= 10:
        score += 0.15
        reasons.append(f"Top holder concentration is elevated at {top_holder_pct:.1f}%")
    elif top_holder_pct >= 6:
        score += 0.08
        reasons.append(f"Top holder concentration is worth watching at {top_holder_pct:.1f}%")
    elif holder_level == "high":
        score += 0.20
        reasons.append("Holder distribution reads high-risk")
    elif holder_level == "warn":
        score += 0.10
        reasons.append("Holder distribution reads concentrated")

    if liq_usd > 0 and vol5 >= liq_usd * 3.0:
        score += 0.12
        reasons.append("Volume is running too hot versus liquidity")
    elif liq_usd > 0 and vol5 >= liq_usd * 1.5:
        score += 0.06
        reasons.append("Volume is elevated relative to liquidity")

    if age > 0 and age <= 10 and liq_usd < 8000:
        score += 0.05
        reasons.append("Very early token with a thin order book")

    return max(0.0, min(score, 1.0)), reasons


def _rug_assessment(
    dex_summary: dict[str, Any],
    holder_risk: dict[str, Any],
    mint_authority: bool,
    freeze_authority: bool,
    liq_locked: bool | None,
) -> dict[str, Any]:
    flags: list[str] = []
    score = 0

    liq = _float(dex_summary.get("liquidity_usd"))
    vol5 = _float(dex_summary.get("volume_m5"))
    top_holder_pct = _float(holder_risk.get("top_holder_pct")) * 100.0
    holder_level = str(holder_risk.get("risk") or "ok")

    if mint_authority:
        score += 3
        flags.append("Mint authority active")
    if freeze_authority:
        score += 3
        flags.append("Freeze authority active")
    if holder_level == "high" or top_holder_pct >= 12:
        score += 3
        flags.append(f"Top holder concentrated at {top_holder_pct:.1f}%")
    elif holder_level == "warn" or top_holder_pct >= 8:
        score += 2
        flags.append(f"Top holder concentration watch at {top_holder_pct:.1f}%")
    if liq_locked is False:
        score += 2
        flags.append("LP not locked")
    if 0 < liq < 5000:
        score += 2
        flags.append(f"Thin liquidity at {_fmt_num(liq, prefix='$')}")
    if liq > 0 and vol5 >= liq * 2.0:
        score += 2
        flags.append("Volume too aggressive for current liquidity")

    verdict = "low"
    if score >= 6:
        verdict = "high"
    elif score >= 3:
        verdict = "watch"

    return {
        "score": score,
        "verdict": verdict,
        "flags": flags,
    }


def _manual_buy_assessment(
    *,
    attention_score: float,
    risk_score: float,
    elite_score: int,
    dex_summary: dict[str, Any],
    security: dict[str, Any],
    rug_check: dict[str, Any],
) -> dict[str, Any]:
    liq = _float(dex_summary.get("liquidity_usd"))
    vol5 = _float(dex_summary.get("volume_m5"))
    buys5 = _int(dex_summary.get("txns_m5_buys"))
    sells5 = _int(dex_summary.get("txns_m5_sells"))
    market_cap = _float(dex_summary.get("market_cap"))
    age = _float(dex_summary.get("age_minutes"))
    buy_sell_ratio = buys5 / max(sells5, 1) if buys5 > 0 else 0.0

    blockers: list[str] = []
    warnings: list[str] = []
    reasons: list[str] = []

    if security.get("mint_authority_active") is True:
        blockers.append("mint_authority_active")
    if security.get("freeze_authority_active") is True:
        blockers.append("freeze_authority_active")
    if str(rug_check.get("verdict") or "").lower() == "high":
        blockers.append("rug_risk_high")
    if not dex_summary:
        blockers.append("dex_pair_missing")
    if liq and liq < 2_000:
        blockers.append("liquidity_too_thin")
    if risk_score >= 0.70:
        blockers.append("risk_score_high")

    if liq < 15_000:
        warnings.append("liquidity_below_review_floor")
    if buys5 < 8:
        warnings.append("buy_breadth_low")
    if buy_sell_ratio < 1.1:
        warnings.append("buy_sell_ratio_not_constructive")
    if market_cap and market_cap > 10_000_000 and vol5 < 25_000:
        warnings.append("market_cap_volume_support_weak")
    if age and age < 1:
        warnings.append("very_new_token")

    if liq >= 15_000:
        reasons.append("tradable_liquidity_observed")
    if buys5 >= 8 and buy_sell_ratio >= 1.1:
        reasons.append("buy_flow_constructive")
    if attention_score >= 0.65:
        reasons.append("legacy_attention_support")
    if risk_score <= 0.35:
        reasons.append("risk_score_contained")
    if elite_score >= 5:
        reasons.append("elite_score_support")

    if blockers:
        action = "HARD_FAIL" if any(item in blockers for item in ("mint_authority_active", "freeze_authority_active", "rug_risk_high")) else "AVOID"
    elif risk_score > 0.50 or len(warnings) >= 3:
        action = "AVOID"
    elif attention_score >= 0.80 and risk_score <= 0.25 and liq >= 25_000 and buys5 >= 15 and buy_sell_ratio >= 1.3:
        action = "VALIDATED_WATCH"
    elif attention_score >= 0.60 and risk_score <= 0.40 and liq >= 15_000 and buys5 >= 8:
        action = "WATCH"
    else:
        action = "OBSERVE"

    return {
        "action": action,
        "mode": "manual_review_shadow",
        "calibration_status": "heuristic_uncalibrated",
        "not_financial_advice": True,
        "summary": {
            "attention_score": round(attention_score, 4),
            "risk_score": round(risk_score, 4),
            "elite_score": elite_score,
            "liquidity_usd": liq,
            "volume_5m": vol5,
            "buys_5m": buys5,
            "sells_5m": sells5,
            "buy_sell_ratio_5m": round(buy_sell_ratio, 4),
        },
        "positive_reasons": reasons[:5],
        "warnings": warnings[:5],
        "blockers": blockers,
        "explanation": "Heuristic CA review only. It does not submit orders, does not enable live trading, and must be validated against executable outcomes before routing use.",
    }


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
        dex_summary,
    )

    lifecycle = "dex" if dex_summary else "bonding_curve"
    rug_check = _rug_assessment(
        dex_summary,
        holder_risk,
        mint_authority,
        freeze_authority,
        liq_locked,
    )
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
            "metric_states": {
                "attention_score": metric_state(attention_score, status="computed", reasons=attention_reasons),
                "risk_score": metric_state(
                    risk_score,
                    status="computed",
                    reasons=risk_reasons,
                ),
                "elite_score": metric_state(elite_score, status="computed"),
                "confidence": metric_state(attention_score, status="computed"),
                "lifecycle": metric_state(lifecycle, status="computed"),
            },
        },
    )
    security = {
        "mint_authority_active": mint_authority,
        "freeze_authority_active": freeze_authority,
        "liquidity_locked": liq_locked,
        "liquidity_drop_spike": liq_drop,
        "holder_risk": holder_risk,
    }
    manual_buy_assessment = _manual_buy_assessment(
        attention_score=attention_score,
        risk_score=risk_score,
        elite_score=elite_score,
        dex_summary=dex_summary,
        security=security,
        rug_check=rug_check,
    )

    result = {
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
        "security": security,
        "rug_check": rug_check,
        "manual_buy_assessment": manual_buy_assessment,
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
        "metric_states": event.extra.get("metric_states"),
    }
    if action_engine_enabled():
        result["action_recommendation"] = ActionEngineService().recommend_for_token(
            token,
            market=result["market"],
            assessment=result,
            intended_size_usd=250.0,
        )
    return result


def render_review_html(review: dict[str, Any]) -> str:
    market = review.get("market") if isinstance(review.get("market"), dict) else {}
    security = review.get("security") if isinstance(review.get("security"), dict) else {}
    social = review.get("social") if isinstance(review.get("social"), dict) else {}
    links = review.get("links") if isinstance(review.get("links"), dict) else {}
    rug_check = review.get("rug_check") if isinstance(review.get("rug_check"), dict) else {}
    metric_states = review.get("metric_states") if isinstance(review.get("metric_states"), dict) else {}
    manual_buy = review.get("manual_buy_assessment") if isinstance(review.get("manual_buy_assessment"), dict) else {}

    token_raw = str(review.get("token") or "")
    symbol_raw = str(review.get("symbol") or "UNK").upper()
    name_raw = str(review.get("name") or symbol_raw)
    lifecycle = str(review.get("lifecycle") or "unknown")
    attention = to_optional_float(review.get("attention_score"))
    risk = to_optional_float(review.get("risk_score"))
    elite = review.get("elite_score") if isinstance(review.get("elite_score"), int) else None
    confidence = attention

    vm = SignalViewModel(
        token=token_raw,
        symbol=symbol_raw,
        name=name_raw,
        lifecycle=lifecycle,
        attention_score=attention,
        risk_score=risk,
        confidence_score=confidence,
        elite_score=elite,
        market=market,
        social=social,
        links=links,
    )

    signal_class = signal_type("review", vm.attention_score, vm.risk_score)
    title = html.escape(signal_title(signal_class, vm.symbol))
    quick_read = html.escape(summary_blurb(vm.attention_score, vm.risk_score, vm.lifecycle))
    manual_action = str(manual_buy.get("action") or "OBSERVE")
    lifecycle_label_raw = (
        "Solana DEX"
        if vm.lifecycle == "dex"
        else "Pump.fun Curve"
        if vm.lifecycle == "bonding_curve"
        else vm.lifecycle.title()
    )
    lifecycle_label = html.escape(lifecycle_label_raw)
    confidence_value = (
        f"{int(round(vm.confidence_score * 100))}%"
        if vm.confidence_score is not None
        else metric_label(get_metric_meta(review, "confidence"))
    )
    risk_value = f"{risk:.2f}" if risk is not None else metric_label(get_metric_meta(review, "risk_score"))
    attention_value = (
        f"{attention:.2f}" if attention is not None else metric_label(get_metric_meta(review, "attention_score"))
    )
    conviction = html.escape(
        "Momentum confirmed"
        if vm.attention_score is not None and vm.attention_score >= 0.85 and (vm.risk_score is None or vm.risk_score <= 0.15)
        else "Early follow-through"
        if vm.attention_score is not None and vm.attention_score >= 0.65
        else "Developing flow"
        if vm.attention_score is not None
        else "Not computed"
    )
    decision_strip = " ".join(
        [
            f"<span class=\"metric-pill\">ACTION {html.escape(manual_action)}</span>",
            f"<span class=\"metric-pill\">CONF {html.escape(confidence_value)}</span>",
            f"<span class=\"metric-pill\">RISK {html.escape(risk_value)}</span>",
            f"<span class=\"metric-pill\">ATTN {html.escape(attention_value)}</span>",
            f"<span class=\"metric-pill\">ROUTE {html.escape(lifecycle_label_raw.upper() if lifecycle_label_raw else 'N/A')}</span>",
        ]
    )

    buys = market.get("txns_m5_buys") if isinstance(market.get("txns_m5_buys"), int) else _int(market.get("txns_m5_buys")) if market.get("txns_m5_buys") is not None else None
    sells = market.get("txns_m5_sells") if isinstance(market.get("txns_m5_sells"), int) else _int(market.get("txns_m5_sells")) if market.get("txns_m5_sells") is not None else None
    flow_bias = flow_bias_label(buys, sells) or "Unavailable"
    momentum = momentum_label(vm.attention_score, market)
    structure = f"{score_band(vm.attention_score)} attention / {score_band(vm.risk_score, invert=True)} risk"
    tier = quality_tier(vm.attention_score, vm.risk_score, vm.elite_score)

    holder_risk = security.get("holder_risk") if isinstance(security.get("holder_risk"), dict) else {}
    holder_distribution = "Concentrated" if str(holder_risk.get("risk") or "") in ("warn", "high") else "Organic / unknown"
    top_holder = to_optional_float(holder_risk.get("top_holder_pct"))
    rug_score = _int(rug_check.get("score"))
    rug_verdict = str(rug_check.get("verdict") or "low").upper()
    rug_flags = rug_check.get("flags") if isinstance(rug_check.get("flags"), list) else []
    attention_reasons = review.get("attention_reasons") if isinstance(review.get("attention_reasons"), list) else []
    risk_reasons = review.get("risk_reasons") if isinstance(review.get("risk_reasons"), list) else []
    reasons = [str(item) for item in (attention_reasons + risk_reasons) if isinstance(item, str)][:5]

    chips = [
        ("Confidence", confidence_value, confidence_band(vm.confidence_score)),
        ("Risk", risk_value, risk_band(vm.risk_score)),
        ("Attention", attention_value, score_band(vm.attention_score)),
        ("Solana Route", lifecycle_label, ""),
        ("Tier", tier, ""),
    ]

    market_strip_parts = [
        f"LIQ {format_currency_compact(market.get('liquidity_usd'))}" if market.get("liquidity_usd") is not None else "",
        f"MC {format_currency_compact(market.get('market_cap'))}" if market.get("market_cap") is not None else "",
        f"VOL5 {format_currency_compact(market.get('volume_m5'))}" if market.get("volume_m5") is not None else "",
        f"AGE {_fmt_num(market.get('age_minutes'), 1)}m" if market.get("age_minutes") is not None else "",
        f"M5 {format_percent_compact(market.get('price_change_m5'), decimals=1)}" if market.get("price_change_m5") is not None else "",
    ]
    market_strip = " ".join(
        f"<span class=\"metric-pill\">{html.escape(part)}</span>" for part in market_strip_parts if part
    )
    liq_mc = "--"
    if to_optional_float(market.get("market_cap")) and to_optional_float(market.get("liquidity_usd")) is not None:
        liq_mc = f"{round((float(market.get('liquidity_usd')) / float(market.get('market_cap'))) * 100)}%"

    def chip(label: str, value: str, sub: str) -> str:
        return (
            f'<div class="chip"><span>{html.escape(label)}</span>'
            f"<strong>{html.escape(value)}</strong>"
            f'<em>{html.escape(sub)}</em></div>'
        )

    def panel(title_text: str, body: str) -> str:
        return f'<section class="panel"><h3>{html.escape(title_text)}</h3>{body}</section>'

    def kv(label: str, value: str) -> str:
        return f'<div class="kv"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'

    def link_button(label: str, url: Any) -> str:
        if not isinstance(url, str) or not url:
            return ""
        safe_url = html.escape(url, quote=True)
        return f'<a class="link-btn" href="{safe_url}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'

    def safe_join(items: list[str]) -> str:
        return "".join(item for item in items if item)

    social_bits = []
    if social.get("tweet_count") or social.get("unique_authors"):
        social_bits.append(kv("X Momentum", f"{_int(social.get('tweet_count'))} mentions / {_int(social.get('unique_authors'))} authors"))
    if social.get("likes"):
        social_bits.append(kv("X Likes", str(_int(social.get("likes")))))

    reason_items = "".join(f"<li>{html.escape(item)}</li>" for item in reasons)
    quality_bits = [
        kv("Confidence", f"{confidence_value} ({confidence_band(vm.confidence_score)})"),
        kv("Risk Score", f"{risk_value} ({risk_band(vm.risk_score)})"),
        kv("Elite Score", str(vm.elite_score) if vm.elite_score is not None else metric_label(get_metric_meta(review, "elite_score"))),
        kv("Tier", tier),
        kv("Holder Distribution", holder_distribution),
    ]
    action_links = safe_join(
        [
            link_button("Dexscreener", links.get("dexscreener")),
            link_button("Birdeye", links.get("birdeye")),
            link_button("X", links.get("twitter_url")),
            link_button("Web", links.get("website_url")),
            link_button("TG", links.get("telegram_url")),
        ]
    )
    social_html = safe_join(social_bits) if social_bits else kv("X Momentum", "N/A")
    token_panel = panel(
        "Token Identity",
        (
            '<div class="stack">'
            f'<div><strong>Token:</strong> {html.escape(vm.name)} <span class="mono">${html.escape(vm.symbol)}</span></div>'
            f'<div class="mono"><strong>Contract:</strong> {html.escape(vm.token)}</div>'
            "</div>"
        ),
    )
    actions_panel = panel(
        "Actions",
        f'<div class="links">{action_links}</div>',
    )
    market_panel = panel(
        "Market Snapshot",
        (
            '<div class="stack">'
            f'<div>{market_strip}</div>'
            '<div class="kv-grid">'
            f'{kv("Liquidity", format_currency_compact(market.get("liquidity_usd")))}'
            f'{kv("Market Cap", format_currency_compact(market.get("market_cap")))}'
            f'{kv("5m Volume", format_currency_compact(market.get("volume_m5")))}'
            f'{kv("Age", (_fmt_num(market.get("age_minutes"), 1) + "m") if market.get("age_minutes") is not None else "N/A")}'
            f'{kv("5m Change", format_percent_compact(market.get("price_change_m5"), decimals=1))}'
            f'{kv("Liq / MC", liq_mc)}'
            "</div>"
            "</div>"
        ),
    )
    flow_panel = panel(
        "Flow + Structure",
        (
            '<div class="kv-grid">'
            f'{kv("5m Buy Flow", str(buys) if buys is not None else "N/A")}'
            f'{kv("5m Sell Flow", str(sells) if sells is not None else "N/A")}'
            f'{kv("Flow Bias", flow_bias)}'
            f'{kv("Momentum", momentum)}'
            f'{kv("Structure", structure)}'
            f'{kv("Lifecycle", lifecycle_label_raw)}'
            "</div>"
        ),
    )
    quality_panel = panel("Quality", f'<div class="kv-grid">{"".join(quality_bits)}</div>')
    manual_reasons = manual_buy.get("positive_reasons") if isinstance(manual_buy.get("positive_reasons"), list) else []
    manual_warnings = manual_buy.get("warnings") if isinstance(manual_buy.get("warnings"), list) else []
    manual_blockers = manual_buy.get("blockers") if isinstance(manual_buy.get("blockers"), list) else []
    manual_panel = panel(
        "Manual Buy Assessment",
        (
            '<div class="kv-grid">'
            f'{kv("Action", manual_action)}'
            f'{kv("Mode", str(manual_buy.get("mode") or "manual_review_shadow"))}'
            f'{kv("Calibration", str(manual_buy.get("calibration_status") or "heuristic_uncalibrated"))}'
            "</div>"
            f'<div class="subtitle">Reasons: {html.escape(", ".join(str(item) for item in manual_reasons) or "N/A")}</div>'
            f'<div class="subtitle">Warnings: {html.escape(", ".join(str(item) for item in manual_warnings) or "N/A")}</div>'
            f'<div class="subtitle">Blockers: {html.escape(", ".join(str(item) for item in manual_blockers) or "N/A")}</div>'
        ),
    )
    intelligence_panel = panel(
        "Signal Intelligence",
        f'<ul class="list">{reason_items}</ul>' if reason_items else '<div class="subtitle">No trigger explanation available.</div>',
    )
    security_panel = panel(
        "Security",
        (
            '<div class="kv-grid">'
            f'{kv("Risk Score", f"{risk_value} ({risk_band(vm.risk_score)})")}'
            f'{kv("Elite Score", str(vm.elite_score) if vm.elite_score is not None else metric_label(get_metric_meta(review, "elite_score")))}'
            f'{kv("Holder Distribution", holder_distribution)}'
            f'{kv("Top Holder", format_percent_compact((top_holder or 0.0) * 100.0, decimals=1) if top_holder is not None else "N/A")}'
            f'{kv("Mint Auth", "ON" if security.get("mint_authority_active") else "OFF")}'
            f'{kv("Freeze Auth", "ON" if security.get("freeze_authority_active") else "OFF")}'
            f'{kv("LP Locked", "YES" if security.get("liquidity_locked") is True else "NO" if security.get("liquidity_locked") is False else "UNK")}'
            f'{kv("Rug Check", f"{rug_verdict} / {rug_score}")}'
            "</div>"
            f'<div class="subtitle">{html.escape(", ".join(rug_flags) if rug_flags else "No major rug flags")}</div>'
        ),
    )
    social_panel = panel(
        "Social",
        f'<div class="kv-grid">{social_html}</div>',
    )

    html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Signal Engine Review // {html.escape(vm.symbol)}</title>
  <style>
    :root {{
      --bg: #07111a;
      --panel: rgba(10, 20, 31, 0.86);
      --panel-2: rgba(15, 30, 46, 0.94);
      --line: rgba(120, 160, 198, 0.16);
      --text: #edf4fb;
      --muted: #89a1b5;
      --accent: {signal_color(signal_class, vm.risk_score)};
      --shadow: 0 24px 70px rgba(0,0,0,.42);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", "SF Pro Display", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(244,196,48,.18), transparent 24%),
        radial-gradient(circle at bottom right, rgba(47,107,255,.18), transparent 26%),
        linear-gradient(180deg, #061019 0%, #09131d 100%);
    }}
    .shell {{ max-width: 1240px; margin: 0 auto; padding: 28px 18px 40px; }}
    .hero {{ display:grid; grid-template-columns:1.35fr .65fr; gap:18px; margin-bottom:18px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
      padding: 22px;
    }}
    .command {{
      background:
        linear-gradient(135deg, rgba(244,196,48,.10), rgba(47,107,255,.08)),
        var(--panel);
    }}
    .eyebrow {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .18em; }}
    .title {{ display:flex; justify-content:space-between; gap:16px; margin:12px 0 10px; align-items:flex-start; }}
    h1 {{ margin:0; font-size: clamp(28px, 4vw, 42px); line-height: 1; }}
    .subtitle {{ color: var(--muted); margin-top: 10px; max-width: 760px; line-height: 1.5; }}
    .strip {{ margin-top: 18px; font-size: 14px; line-height: 1.9; display:flex; flex-wrap:wrap; gap:8px; }}
    .conviction {{ margin-top: 10px; font-size: 15px; }}
    .badge {{
      padding: 10px 14px; border-radius: 999px; border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
      color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent); font-weight: 700; white-space: nowrap;
      letter-spacing: .12em; text-transform: uppercase; font-size: 12px;
    }}
    .chips {{ display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: 10px; margin-top: 18px; }}
    .chip {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 18px; padding: 12px 14px; }}
    .chip span, .kv span {{ display:block; font-size:11px; color: var(--muted); text-transform: uppercase; letter-spacing: .12em; margin-bottom: 6px; }}
    .chip strong, .kv strong {{ display:block; font-size: 18px; }}
    .chip em {{ display:block; margin-top: 6px; color: var(--muted); font-style: normal; font-size: 12px; }}
    .metric-pill {{
      display:inline-flex; align-items:center; padding:6px 10px; border-radius:999px; border:1px solid var(--line);
      background: var(--panel-2); color: var(--text); font-size:12px; letter-spacing:.08em; text-transform: uppercase;
    }}
    .grid {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 18px; }}
    .grid-wide {{ display:grid; grid-template-columns: 1.2fr .8fr; gap: 18px; margin-bottom: 18px; }}
    h3 {{ margin:0 0 14px; font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: .16em; }}
    .mono {{ font-family: "Consolas", "SFMono-Regular", monospace; }}
    .kv-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; }}
    .kv {{ background: var(--panel-2); border:1px solid var(--line); border-radius: 16px; padding: 12px 14px; }}
    .stack {{ display:grid; gap: 10px; }}
    .list {{ margin:0; padding-left: 18px; }}
    .list li {{ margin-bottom: 8px; line-height: 1.45; }}
    .links {{ display:flex; flex-wrap:wrap; gap: 10px; }}
    .link-btn {{
      text-decoration:none; color: var(--text); background: var(--panel-2); border:1px solid var(--line);
      border-radius:999px; padding:10px 14px; font-size:13px; font-weight:600;
    }}
    .footer {{ margin-top:18px; padding: 14px 18px; border:1px solid var(--line); border-radius: 18px; color: var(--muted); letter-spacing: .08em; text-transform: uppercase; font-size: 12px; }}
    @media (max-width: 1020px) {{
      .hero, .grid-wide, .grid {{ grid-template-columns: 1fr; }}
      .chips {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
    }}
    @media (max-width: 640px) {{
      .shell {{ padding: 14px 12px 28px; }}
      .title {{ flex-direction: column; }}
      .chips, .kv-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel command">
      <div class="eyebrow">Signal Engine Review</div>
      <div class="title">
        <div>
          <h1>{title}</h1>
          <div class="subtitle">{quick_read}</div>
        </div>
        <div class="badge">{html.escape(quality_tier(vm.attention_score, vm.risk_score, vm.elite_score))}</div>
      </div>
      <div class="strip">{decision_strip}</div>
      <div class="conviction"><strong>Conviction:</strong> {conviction}</div>
      <div class="chips">
        {''.join(chip(label, value, sub) for label, value, sub in chips)}
      </div>
    </section>
    <div class="grid-wide">
      {token_panel}
      {actions_panel}
    </div>
    <div class="grid">
      {market_panel}
      {flow_panel}
      {manual_panel}
      {quality_panel}
      {intelligence_panel}
      {security_panel}
      {social_panel}
    </div>
    <div class="footer">Signal Engine  Radar Deck  review</div>
  </div>
</body>
</html>"""
    return html_out
