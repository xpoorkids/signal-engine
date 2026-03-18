"""
Risk aggregation layer for promotion and candidate veto logic.

Purpose
-------
- Builds the runtime risk view used by `worker.promote` to veto or downgrade
  tokens before candidate or promoted alerts.
- Combines live state-derived risk features with structural token/liquidity
  flags and delegates the weighted scoring to
  `app.services.signal_metrics.compute_risk_score()`.
- This module does not own threshold policy; it produces the risk score,
  reasons, and flags that downstream routing evaluates.

Runtime data flow
-----------------
Inputs:
- Current `Event`
- `EngineState` methods for:
  - wallet clustering
  - liquidity stability
  - top holder concentration
  - bot-like trade cadence
- Optional structural/risk inputs from other enrichers:
  - wallet risk payload
  - mint authority
  - freeze authority
  - liquidity USD
  - liquidity locked
  - liquidity drop spike

Transformations:
1. Probe state for concentration, liquidity, and cadence features.
2. Convert those observations into coarse `risk_flags`.
3. Delegate numeric scoring and reason generation to
   `compute_risk_score()`.
4. Apply an extra penalty and explicit reason when liquidity is observed as
   unstable over the local state window.
5. Return both the numeric risk and the raw metric payload for downstream
   diagnostics.

Outputs:
- `risk_score`
- `risk_reasons`
- `risk_flags`
- full `risk_metric` payload from `compute_risk_score()`

Key logic
---------
- This module is partly state-derived and partly structural:
  state history can raise risk even when token metadata looks acceptable.
- `liquidity_unstable` is handled twice intentionally:
  - as a boolean operational flag
  - as an extra additive penalty on top of `compute_risk_score()`
- If no usable inputs are available, `compute_risk_score()` returns
  `status="insufficient_data"` and this module returns `risk_score=None`.
- `risk_flags` is broader than the strict numeric reasons; it is meant for
  downstream diagnostics and UI grouping as much as routing.

Failure modes
-------------
- Missing state methods or sparse state:
  - Exceptions are swallowed and the corresponding input is treated as absent.
  - This reduces risk visibility and can yield `risk_score=None`.
- Incomplete upstream enrichment:
  - Missing authority/liquidity inputs lower the quality of the computed risk.
- Risk underestimation:
  - If both state-derived signals and structural inputs are absent, the system
    falls back to downstream confidence logic using a neutral risk fallback.

Logging and observability
-------------------------
- This module does not log directly.
- Downstream observability appears in `worker.promote`, especially:
  - `[risk-gate]`
  - `[risk-score]`
  - confidence component logs using the returned risk score
- For deep debugging, inspect the returned `risk_metric` payload because it
  includes `status`, `reasons`, and `inputs_used`.

Dependencies and config
-----------------------
Internal dependencies:
- `app.services.signal_metrics.compute_risk_score`

Gotchas
-------
- `risk_score=None` means "insufficient data", not "safe".
- The numeric score is only one output; `risk_flags` can still contain useful
  diagnostics even when the score is missing.
- Liquidity instability has custom handling here, so changes to
  `compute_risk_score()` alone do not fully describe runtime risk behavior.
"""

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
    Aggregate stateful and structural risk inputs into the score/reasons used by
    downstream candidate and promotion gates.

    Returns:
      risk_score: float in [0, 1] or `None` when inputs are insufficient
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
