def one_sentence_explanation(candidate: dict, severity: str) -> str:
    m = candidate.get("metrics", {})
    liq = m.get("liquidity", 0)
    vol5m = m.get("volume_5m", 0)
    chg5m = m.get("price_change_5m", 0)
    age = m.get("age_minutes", 0)

    if severity == "pass":
        return (
            f"Escalated to PASS: repeated confirmations with strong flow "
            f"(liq ${liq:,.0f}, 5m vol ${vol5m:,.0f}) while still early (~{age:.0f}m)."
        )
    if severity == "rug":
        return (
            "RUG risk flagged: wallet concentration / dev behavior looks dangerous "
            "relative to liquidity and early flow."
        )
    return (
        "Near-pass because early momentum is building with tradable liquidity "
        f"(liq ${liq:,.0f}, 5m vol ${vol5m:,.0f}, +{chg5m:.1f}% in 5m, age ~{age:.0f}m)."
    )
