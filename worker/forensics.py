from typing import Tuple, List, Dict, Any

from app.services.signal_metrics import compute_risk_score


def analyze_risk(
    e,
    state,
    *,
    wallet_risk: dict[str, Any] | None = None,
    mint_authority: bool | None = None,
    freeze_authority: bool | None = None,
    liq_usd: float | None = None,
    liq_locked: bool | None = None,
    liq_drop_spike: bool | None = None,
) -> Tuple[float | None, List[str], Dict[str, bool], dict[str, Any]]:
    """
    Returns:
      risk_score: float in [0, 1]
      risk_reasons: human-readable list of strings
      risk_flags: dictionary of boolean clusters
    """
    risk_flags: Dict[str, bool] = {}
    try:
        cluster_ratio = state.wallet_cluster_ratio(e.token)
    except Exception:
        cluster_ratio = None
    if isinstance(cluster_ratio, (int, float)) and cluster_ratio > 0.4:
        risk_flags["wallet_cluster"] = True

    try:
        liquidity_stable = state.liquidity_stable(e.token, window_sec=1800)
    except Exception:
        liquidity_stable = None
    if liquidity_stable is False:
        risk_flags["liquidity_unstable"] = True

    try:
        top_ratio = state.top_holder_ratio(e.token)
    except Exception:
        top_ratio = None
    if isinstance(top_ratio, (int, float)) and top_ratio > 0.18:
        risk_flags["holder_concentration"] = True

    try:
        cadence = state.bot_trade_cadence(e.token)
    except Exception:
        cadence = None
    if cadence:
        risk_flags["bot_cadence"] = True

    metric = compute_risk_score(
        wallet_cluster_ratio=cluster_ratio if isinstance(cluster_ratio, (int, float)) else None,
        liquidity_stable=liquidity_stable if isinstance(liquidity_stable, bool) else None,
        top_holder_ratio=top_ratio if isinstance(top_ratio, (int, float)) else None,
        bot_trade_cadence=cadence if isinstance(cadence, bool) else None,
        mint_authority=mint_authority,
        freeze_authority=freeze_authority,
        liq_usd=liq_usd,
        liq_locked=liq_locked,
        liq_drop_spike=liq_drop_spike,
        wallet_risk=wallet_risk,
    )
    if liquidity_stable is False:
        metric["reasons"] = list(metric.get("reasons") or []) + ["liquidity_unstable"]
        value = metric.get("value")
        metric["value"] = min(1.0, (0.0 if value is None else float(value)) + 0.20)
    if metric.get("status") == "computed" and metric.get("value") is not None:
        risk_score = float(metric["value"])
    else:
        risk_score = None

    if liquidity_stable is False:
        risk_flags["liquidity_unstable"] = True
    if metric.get("reasons"):
        for reason in metric["reasons"]:
            if reason in ("wallet_clustering_high", "wallet_clustering_watch"):
                risk_flags["wallet_cluster"] = True
            if reason.startswith("top_holder") or reason.startswith("wallet_top_holder"):
                risk_flags["holder_concentration"] = True
            if reason == "bot_like_trade_cadence":
                risk_flags["bot_cadence"] = True

    return risk_score, list(metric.get("reasons") or []), risk_flags, metric
