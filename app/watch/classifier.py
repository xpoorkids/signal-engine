 from __future__ import annotations
 from typing import Any, Dict
 from .stages import StageDecision, WatchStage
 from .stage_config import SOL_STAGE_THRESHOLDS as T
 
 def classify_watch_stage(signals: Dict[str, Any]) -> StageDecision:
     reasons: list[str] = []
     score = 0
 
+    def apply_tiers(
+        value: Any,
+        metric_key: str,
+        label: str,
+        tiers: list[tuple[str, int]],
+        below_score: int,
+        below_reason: str,
+    ) -> None:
+        nonlocal score
+        if not isinstance(value, (int, float)):
+            return
+        thresholds = T[metric_key]
+        for tier_key, tier_score in tiers:
+            if value >= thresholds[tier_key]:
+                score += tier_score
+                reasons.append(f"{label} >= {tier_key}")
+                return
+        score += below_score
+        reasons.append(below_reason)
+
+    def apply_penalty(
+        value: Any,
+        metric_key: str,
+        label: str,
+        tiers: list[tuple[str, int]],
+    ) -> None:
+        nonlocal score
+        if not isinstance(value, (int, float)):
+            return
+        thresholds = T[metric_key]
+        for tier_key, tier_score in tiers:
+            if value >= thresholds[tier_key]:
+                score += tier_score
+                reasons.append(f"{label} >= {tier_key}")
+                return
+
+    if bool(signals.get("rug_bad", False)):
         return StageDecision(
             stage="early",
             score=-999,
             reasons=["rug / critical risk flag"],
             signals=signals,
         )
 
 apply_tiers(
        signals.get("lp_usd"),
        "lp_usd",
        "LP",
        [("high", 3), ("mid", 2), ("min", 1)],
        -2,
        "LP < min",
    )
    apply_tiers(
        signals.get("vol_5m"),
        "vol_5m",
        "Vol5m",
        [("high", 4), ("mid", 3), ("low", 1)],
        -1,
        "Vol5m weak",
    )
    apply_tiers(
        signals.get("tx_5m"),
        "tx_5m",
        "Tx5m",
        [("high", 3), ("mid", 2), ("low", 1)],
        -1,
        "Tx5m sparse",
    )
    apply_tiers(
        signals.get("holders_delta_15m"),
        "holders_delta_15m",
        "Holders",
        [("high", 4), ("mid", 3), ("low", 1)],
        -1,
        "Holder growth weak",
    )
    apply_penalty(
        signals.get("top10_pct"),
        "top10_pct",
        "Top10",
        [("severe", -3), ("bad", -2), ("warn", -1)],
    )

     if score >= T["stage_cutoffs"]["near_pass"]:
         stage: WatchStage = "near_pass"
+    elif score >= T["stage_cutoffs"]["building"]:
         stage = "building"
     else:
         stage = "early"
 
     return StageDecision(stage, score, reasons, signals)
