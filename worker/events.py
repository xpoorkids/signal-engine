from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import time
import uuid


@dataclass
class Event:
    type: str
    source: str
    signature: Optional[str] = None
    program: Optional[str] = None
    token: Optional[str] = None
    creator: Optional[str] = None
    slot: Optional[int] = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=lambda: time.time())
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


def as_dict(e: Event) -> Dict[str, Any]:
    return {
        "id": e.id,
        "type": e.type,
        "source": e.source,
        "signature": e.signature,
        "program": e.program,
        "token": e.token,
        "creator": e.creator,
        "slot": e.slot,
        "confidence": e.confidence,
        "reasons": e.reasons,
        "extra": e.extra,
        "ts": e.ts,
    }
