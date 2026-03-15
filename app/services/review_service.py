import html
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
        "rug_check": rug_check,
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


def render_review_html(review: dict[str, Any]) -> str:
    market = review.get("market") if isinstance(review.get("market"), dict) else {}
    security = review.get("security") if isinstance(review.get("security"), dict) else {}
    social = review.get("social") if isinstance(review.get("social"), dict) else {}
    links = review.get("links") if isinstance(review.get("links"), dict) else {}
    rug_check = review.get("rug_check") if isinstance(review.get("rug_check"), dict) else {}

    name = html.escape(str(review.get("name") or "UNK"))
    symbol = html.escape(str(review.get("symbol") or "UNK"))
    token = html.escape(str(review.get("token") or ""))
    lifecycle = str(review.get("lifecycle") or "unknown")
    lifecycle_label = "DEX" if lifecycle == "dex" else "BONDING CURVE"
    attention = float(review.get("attention_score") or 0.0)
    risk = float(review.get("risk_score") or 0.0)
    elite = int(review.get("elite_score") or 0)

    if attention >= 0.85:
        stage = "BREAKOUT"
    elif attention >= 0.70:
        stage = "SETUP"
    else:
        stage = "WATCH"

    attention_reasons = review.get("attention_reasons") if isinstance(review.get("attention_reasons"), list) else []
    risk_reasons = review.get("risk_reasons") if isinstance(review.get("risk_reasons"), list) else []
    reasons = attention_reasons + risk_reasons
    if not reasons:
        reasons = ["Signals are still developing"]

    x_count = _int(social.get("tweet_count"))
    x_authors = _int(social.get("unique_authors"))
    x_likes = _int(social.get("likes"))
    holder_risk = security.get("holder_risk") if isinstance(security.get("holder_risk"), dict) else {}
    rug_score = _int(rug_check.get("score"))
    rug_verdict = str(rug_check.get("verdict") or "low").upper()
    rug_flags = rug_check.get("flags") if isinstance(rug_check.get("flags"), list) else []

    def chip(label: str, value: str, tone: str = "") -> str:
        tone_class = f" chip-{tone}" if tone else ""
        return f'<div class="chip{tone_class}"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'

    def stat(label: str, value: str) -> str:
        return f'<div class="stat"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'

    def link_button(label: str, url: Any) -> str:
        if not isinstance(url, str) or not url:
            return ""
        safe_url = html.escape(url, quote=True)
        return f'<a class="link-btn" href="{safe_url}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'

    reason_items = "".join(f"<li>{html.escape(str(reason))}</li>" for reason in reasons[:8])

    html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Signal Engine Review // {symbol}</title>
  <style>
    :root {{
      --panel: rgba(11, 24, 36, 0.88);
      --panel-2: rgba(16, 33, 48, 0.92);
      --line: rgba(123, 162, 196, 0.18);
      --text: #e8f0f7;
      --muted: #8ea4b8;
      --gold: #f3bd3f;
      --teal: #49dcb1;
      --red: #ff6b6b;
      --shadow: 0 20px 60px rgba(0,0,0,0.45);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "SF Pro Display", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(243,189,63,0.18), transparent 28%),
        radial-gradient(circle at top right, rgba(73,220,177,0.12), transparent 24%),
        linear-gradient(180deg, #071019 0%, #0b1723 100%);
      color: var(--text);
      min-height: 100vh;
    }}
    .shell {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 18px 40px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: 1.3fr .7fr;
      gap: 18px;
      margin-bottom: 18px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }}
    .hero-main {{
      padding: 22px;
      background:
        linear-gradient(135deg, rgba(243,189,63,0.12), rgba(124,183,255,0.08)),
        var(--panel);
    }}
    .eyebrow {{
      color: var(--gold);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .18em;
      text-transform: uppercase;
    }}
    .title-row {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      margin-top: 10px;
    }}
    .title h1 {{
      margin: 0;
      font-size: clamp(28px, 4vw, 44px);
      line-height: 1;
    }}
    .title .sub {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
      word-break: break-all;
    }}
    .stage {{
      border: 1px solid rgba(243,189,63,0.28);
      color: var(--gold);
      background: rgba(243,189,63,0.08);
      padding: 10px 14px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .14em;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    .chips {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-top: 18px;
    }}
    .chip {{
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: var(--panel-2);
    }}
    .chip span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .12em;
      margin-bottom: 6px;
    }}
    .chip strong {{
      font-size: 18px;
      font-weight: 700;
    }}
    .chip-good strong {{ color: var(--teal); }}
    .chip-warn strong {{ color: var(--gold); }}
    .chip-bad strong {{ color: var(--red); }}
    .hero-side {{
      padding: 22px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 18px;
    }}
    .hero-side h3, .section h3 {{
      margin: 0 0 12px;
      font-size: 13px;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .tape {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .stat {{
      padding: 12px 14px;
      border-radius: 16px;
      background: var(--panel-2);
      border: 1px solid var(--line);
    }}
    .stat span {{
      display: block;
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .11em;
      margin-bottom: 6px;
    }}
    .stat strong {{
      font-size: 18px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 18px;
    }}
    .section {{
      padding: 20px;
    }}
    .list {{
      margin: 0;
      padding-left: 18px;
      color: var(--text);
    }}
    .list li {{
      margin: 0 0 8px;
    }}
    .muted {{
      color: var(--muted);
    }}
    .link-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 10px;
    }}
    .link-btn {{
      text-decoration: none;
      color: var(--text);
      border: 1px solid var(--line);
      background: var(--panel-2);
      padding: 10px 14px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 600;
    }}
    .footer {{
      margin-top: 18px;
      padding: 14px 18px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(7, 16, 25, 0.75);
      color: var(--muted);
      font-size: 12px;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    @media (max-width: 1180px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 980px) {{
      .hero, .grid {{ grid-template-columns: 1fr; }}
      .chips {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      .shell {{ padding: 16px 12px 28px; }}
      .title-row {{ flex-direction: column; }}
      .chips, .tape {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <section class="card hero-main">
        <div class="eyebrow">Signal Engine Review</div>
        <div class="title-row">
          <div class="title">
            <h1>{name} <span class="muted">${symbol}</span></h1>
            <div class="sub">{token}</div>
          </div>
          <div class="stage">SE // {html.escape(stage)}</div>
        </div>
        <div class="chips">
          {chip("Attention", f"{attention * 100:.1f}%", "good" if attention >= 0.7 else "warn")}
          {chip("Risk", f"{risk * 100:.1f}%", "bad" if risk >= 0.4 else "warn" if risk >= 0.2 else "good")}
          {chip("Rug", rug_verdict, "bad" if rug_verdict == "HIGH" else "warn" if rug_verdict == "WATCH" else "good")}
          {chip("Elite", str(elite), "good" if elite >= 8 else "warn" if elite >= 4 else "bad")}
          {chip("Lifecycle", lifecycle_label, "warn" if lifecycle == "bonding_curve" else "good")}
        </div>
      </section>
      <aside class="card hero-side">
        <div>
          <h3>Tape</h3>
          <div class="tape">
            {stat("Liquidity", _fmt_num(market.get("liquidity_usd"), prefix="$"))}
            {stat("Market Cap", _fmt_num(market.get("market_cap"), prefix="$"))}
            {stat("Volume 5m", _fmt_num(market.get("volume_m5"), prefix="$"))}
            {stat("Volume 1h", _fmt_num(market.get("volume_h1"), prefix="$"))}
            {stat("M5 Change", _fmt_pct(market.get("price_change_m5"), 1))}
            {stat("Age", _fmt_num(market.get("age_minutes"), 1) + "m" if market.get("age_minutes") is not None else "--")}
          </div>
        </div>
        <div>
          <h3>Links</h3>
          <div class="link-row">
            {link_button("DexScreener", links.get("dexscreener"))}
            {link_button("Birdeye", links.get("birdeye"))}
            {link_button("Website", links.get("website_url"))}
            {link_button("X", links.get("twitter_url"))}
            {link_button("Telegram", links.get("telegram_url"))}
          </div>
        </div>
      </aside>
    </div>
    <div class="grid">
      <section class="card section">
        <h3>Why It Triggered</h3>
        <ul class="list">{reason_items}</ul>
      </section>
      <section class="card section">
        <h3>Rug Check</h3>
        <div class="tape">
          {stat("Verdict", rug_verdict)}
          {stat("Rug Score", str(rug_score))}
          {stat("Top Holder", _fmt_pct(_float(holder_risk.get("top_holder_pct")) * 100.0, 1))}
          {stat("Vol / Liq", f"{(_float(market.get('volume_m5')) / _float(market.get('liquidity_usd'))):.1f}x" if _float(market.get('liquidity_usd')) > 0 else "--")}
        </div>
        <div style="margin-top:12px" class="muted">{html.escape(', '.join(rug_flags) if rug_flags else 'No major rug flags')}</div>
      </section>
      <section class="card section">
        <h3>Security</h3>
        <div class="tape">
          {stat("Mint Auth", "ON" if security.get("mint_authority_active") else "OFF")}
          {stat("Freeze Auth", "ON" if security.get("freeze_authority_active") else "OFF")}
          {stat("LP Locked", "YES" if security.get("liquidity_locked") is True else "NO" if security.get("liquidity_locked") is False else "UNK")}
          {stat("Holder Risk", str(holder_risk.get("risk") or "ok").upper())}
        </div>
        <div style="margin-top:12px" class="muted">{html.escape(str(holder_risk.get("reason") or "holder_ok"))}</div>
      </section>
      <section class="card section">
        <h3>Social</h3>
        <div class="tape">
          {stat("X Mentions", str(x_count))}
          {stat("Authors", str(x_authors))}
          {stat("Likes", str(x_likes))}
          {stat("24h Move", _fmt_pct(market.get("price_change_h24"), 1))}
        </div>
      </section>
    </div>
    <div class="footer">Signal Engine // Contract Intelligence Deck</div>
  </div>
</body>
</html>"""
    return html_out
