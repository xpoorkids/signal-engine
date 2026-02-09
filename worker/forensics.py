from typing import Tuple, List, Dict


def analyze_risk(e, state) -> Tuple[float, List[str], Dict[str, bool]]:
    """
    Returns:
      risk_score: float in [0, 1]
      risk_reasons: human-readable list of strings
      risk_flags: dictionary of boolean clusters
    """
    risk_score = 0.0
    risk_reasons: List[str] = []
    risk_flags: Dict[str, bool] = {}

    # Wallet clustering (stub logic)
    try:
        cluster_ratio = state.wallet_cluster_ratio(e.token)
    except Exception:
        cluster_ratio = 0.0
    if cluster_ratio > 0.5:
        risk_score += 0.35
        risk_reasons.append("wallet_clustering_high")
        risk_flags["wallet_cluster"] = True

    # Liquidity stability (stub logic)
    try:
        if not state.liquidity_stable(e.token, window_sec=1800):
            risk_score += 0.30
            risk_reasons.append("liquidity_unstable")
            risk_flags["liquidity_unstable"] = True
    except Exception:
        pass

    # Top holder concentration (stub logic)
    try:
        top_ratio = state.top_holder_ratio(e.token)
    except Exception:
        top_ratio = 0.0
    if top_ratio > 0.25:
        risk_score += 0.25
        risk_reasons.append("holder_concentration")
        risk_flags["holder_concentration"] = True

    # Behavioral churn / cadence (stub logic)
    try:
        if state.bot_trade_cadence(e.token):
            risk_score += 0.20
            risk_reasons.append("bot_like_trade_cadence")
            risk_flags["bot_cadence"] = True
    except Exception:
        pass

    # clamp risk_score
    risk_score = min(risk_score, 1.0)

    return risk_score, risk_reasons, risk_flags
