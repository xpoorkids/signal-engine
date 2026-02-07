from dataclasses import dataclass


@dataclass
class Candidate:
    token: str
    source: str
    age_seconds: float
    liquidity: float = 0
    volume_5m: float = 0
    momentum_5m: float = 0
    stage: str = "early"

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "source": self.source,
            "reason": self.stage,
            "metrics": {
                "liquidity": self.liquidity,
                "volume_5m": self.volume_5m,
                "price_change_5m": self.momentum_5m,
                "age_minutes": self.age_seconds / 60.0,
            },
        }
