from datetime import datetime, timezone


def score_pairs(pairs: list[dict]) -> list[dict]:
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    out = []

    for p in pairs:
        try:
            liq = float(p.get("liquidity", {}).get("usd") or 0)
            vol5m = float(p.get("volume", {}).get("m5") or 0)
            chg5m = float(p.get("priceChange", {}).get("m5") or 0)
            created = p.get("pairCreatedAt")
            if not created:
                continue

            age = (now_ms - created) / 60000

            if age <= 0.5 and liq >= 800 and vol5m >= 20 and chg5m >= -10:
                out.append(
                    {
                        "token": p["baseToken"]["address"],
                        "symbol": p["baseToken"]["symbol"],
                        "reason": "aggressive_near_pass",
                        "metrics": {
                            "liquidity": round(liq, 2),
                            "volume_5m": round(vol5m, 2),
                            "price_change_5m": round(chg5m, 2),
                            "age_minutes": round(age, 1),
                        },
                    }
                )

        except Exception:
            continue

    return out
