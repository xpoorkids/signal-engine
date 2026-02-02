def build_packet(symbol: str):
    return {
        "symbol": symbol,
        "summary": "Passed initial velocity and liquidity filters",
        "risk_notes": [
            "Early signal",
            "No automated execution",
            "Human confirmation required"
        ]
    }
