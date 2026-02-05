from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Dict, Any

WatchStage = Literal["early", "building", "near_pass"]

@dataclass(frozen=True)
class StageDecision:
    stage: WatchStage
    score: int
    reasons: list[str]
    signals: Dict[str, Any]
