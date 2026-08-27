from __future__ import annotations

from dataclasses import dataclass


PROGRAM_REGISTRY_VERSION = "solana-program-registry-v1-2026-08-27"


@dataclass(frozen=True)
class ProgramInfo:
    program_id: str
    name: str
    category: str
    evidence_source: str


PROGRAMS: dict[str, ProgramInfo] = {
    "11111111111111111111111111111111": ProgramInfo("11111111111111111111111111111111", "System Program", "core", "solana docs and observed account keys"),
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": ProgramInfo("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "SPL Token", "token_program", "spl token program id"),
    "TokenzQdBNbLqP5VEhdkAS6EPFQbrvyuvcxUd7Ue2c": ProgramInfo("TokenzQdBNbLqP5VEhdkAS6EPFQbrvyuvcxUd7Ue2c", "Token-2022", "token_program", "spl token-2022 program id"),
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": ProgramInfo("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "Associated Token Program", "token_account", "spl associated token account program id"),
    "ComputeBudget111111111111111111111111111111": ProgramInfo("ComputeBudget111111111111111111111111111111", "Compute Budget Program", "core", "solana compute budget program id"),
    "6EF8rrecthR5DkJdS7rxBejfsBjgY6T5QYq6LL9pump": ProgramInfo("6EF8rrecthR5DkJdS7rxBejfsBjgY6T5QYq6LL9pump", "Pump.fun", "launchpad", "observed pump.fun program id"),
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": ProgramInfo("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "PumpSwap", "dex", "observed pumpswap program id"),
    "CPMMoo8L3F4NbTegBCKVNdioR1P6ZMXmG8t4P5zXQf6": ProgramInfo("CPMMoo8L3F4NbTegBCKVNdioR1P6ZMXmG8t4P5zXQf6", "Raydium CPMM", "dex", "observed raydium cpmm program id"),
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": ProgramInfo("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", "Meteora DLMM", "dex", "observed meteora dlmm program id"),
    "whirLbMiicVdio4qvUfM5KAg6CtQ5dqZFn1U74KjY8i": ProgramInfo("whirLbMiicVdio4qvUfM5KAg6CtQ5dqZFn1U74KjY8i", "Orca Whirlpool", "dex", "observed orca whirlpool program id"),
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": ProgramInfo("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "Jupiter Aggregator v6", "aggregator", "observed jupiter program id"),
}


def identify_program(program_id: str | None) -> ProgramInfo | None:
    if not program_id:
        return None
    return PROGRAMS.get(program_id)


def detect_launchpad(program_ids: list[str]) -> str | None:
    names = [PROGRAMS[item].name for item in program_ids if item in PROGRAMS and PROGRAMS[item].category == "launchpad"]
    return names[0] if names else None


def detect_venue(program_ids: list[str]) -> str | None:
    for item in program_ids:
        info = PROGRAMS.get(item)
        if info and info.category in {"dex", "aggregator", "launchpad"}:
            return info.name
    return None
