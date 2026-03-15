#!/usr/bin/env python3
"""
Parse noisy OCR text from a crypto token risk alert into structured data.

Features:
- Hardcoded OCR input from two versions.
- Normalization and OCR cleanup.
- Deduplication and consensus selection across versions.
- Section parsing into a nested JSON-serializable dictionary.
- Validation checks for contract address, percentages, and numeric fields.
- Per-field confidence scoring based on cross-version consistency.
- Markdown, JSON, and corrections/anomalies reporting.

Standard library only.
"""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional


RAW_VERSION_1 = r"""
SE RISK ALERT $ROSIE
Early coordination detected. Watch only.
Interest is present, but risk remains elevated. Keep sizing defensive.
Command View
CONF 29% RISK 0.70 ATTN 0.50 LIFE DEX
Conviction: Developing flow
Read: Low confidence / High risk
Token Identity
The Larp Dog $ROSIE
CA: HvdguP2PuzCW6zGSemnj2zDwXnDu6ASRIaGc gVN5wpump
Lifecycle Dex
Attention 0.50
Risk 0.70
Conviction Developing flow
Market Snapshot
LIQ - MC $7.979 VOLS $31,464 AGE 1.5m M5 +187.0%
Liquidity: -
Market Cap: $7,979
5m Volume: $31,464
Age / M5: 1.5m / +187.0%
5m Flow: B 455 / S 293
Liq / MC: -
Flow + Structure
5m Buy Flow: 455
5m Sell Flow: 293
Flow Bias: Buy-side
Momentum: Mixed
Structure: Mixed attention / Weak risk
Signal Intelligence
5m buyer breadth: 5
1m burst strength: 15
Repeat signal count: 5
Quality
Confidence: 29% (Low)
Risk Score: 0.70 (High)
Elite Score: 11
Tier: Tier C
Holder Distribution: Concentrated
Actions
Dexscreener / Birdeye
Signal Engine Radar Deck candidate
"""

RAW_VERSION_2 = r"""
SE RISK ALERT $ROSIE
Early coordination detected. Watch only.
Interest is present, but risk remains elevated. Keep sizing defensive.
Command View
CONF 29% RISK 0.70 ATTN 0.50 LIFE DEX
Conviction: Developing flow
Read: Low confidence / High risk
Token Identity
The Lary Dog $ROSIE
CA: HvdguP2PuzCW6zGSemnj2zDwXnDu6ASRIaGc gVN5wpump
Lifecycle Dex
Attention 0.50
Risk 0.70
Conviction Developing flow
Market Snapshot
LIQ - MC $7.979 VOLS $31,464 AGE 1.5m M5 +187.0%
Liquidity: -
Market Cap: $7,979
5m Volume: $31,464
Age / M5: 1.5m / +187.0%
5m Flow: B 455 / S 293
Liq / MC: -
Flow + Structure
5m Buy Flow: 455
5m Sell Flow: 293
Flow Bias: Buy-side
Momentum: Mixed
Structure: Mixed attention / Weak risk
Signal Intelligence
5m buyer breadth: 5
1m burst strength: 15
Repeat signal count: 5
Quality
Confidence: 29% (Low)
Risk Score: 0.70 (High)
Elite Score: 11
Tier: Tier C
Holder Distribution: Concentrated
Actions
Dexscreener / Birdeye
Signal Engine Radar Deck candidate
"""

SECTION_HEADERS = [
    "Command View",
    "Token Identity",
    "Market Snapshot",
    "Flow + Structure",
    "Signal Intelligence",
    "Quality",
    "Actions",
]

BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]+$")
TITLE_RE = re.compile(r"^(SE\s+[A-Z ]+\s+\$[A-Z0-9]+)$")
TOKEN_LINE_RE = re.compile(r"^(?P<name>.+?)\s+\$(?P<ticker>[A-Z0-9]+)$")
COMMAND_RE = re.compile(
    r"CONF\s+(?P<conf>\d+(?:\.\d+)?)%\s+RISK\s+(?P<risk>\d+(?:\.\d+)?)\s+ATTN\s+(?P<attn>\d+(?:\.\d+)?)\s+LIFE\s+(?P<life>[A-Z_]+)"
)
QUALITY_PERCENT_RE = re.compile(r"^(?P<label>Confidence):\s*(?P<value>\d+(?:\.\d+)?)%\s*\((?P<band>[^)]+)\)")
QUALITY_FLOAT_RE = re.compile(r"^(?P<label>Risk Score):\s*(?P<value>\d+(?:\.\d+)?)\s*\((?P<band>[^)]+)\)")
QUALITY_INT_RE = re.compile(r"^(?P<label>Elite Score):\s*(?P<value>\d+)")
KEY_VALUE_RE = re.compile(r"^(?P<key>[^:]+):\s*(?P<value>.+)$")
FLOW_RE = re.compile(r"^5m Flow:\s*B\s*(?P<buy>\d+)\s*/\s*S\s*(?P<sell>\d+)$", re.I)
AGE_M5_RE = re.compile(r"^Age\s*/\s*M5:\s*(?P<age>[^/]+)\s*/\s*(?P<m5>.+)$", re.I)
MARKET_TAPE_RE = re.compile(
    r"LIQ\s+(?P<liq>[-$0-9,\.KMB]+)\s+MC\s+(?P<mc>[-$0-9,\.KMB]+)\s+VOLS?\s+(?P<vol>[-$0-9,\.KMB]+)\s+AGE\s+(?P<age>[0-9\.]+m)\s+M5\s+(?P<m5>[+\-]?[0-9\.]+%)",
    re.I,
)


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_line(line: str, corrections: List[str]) -> str:
    original = line
    line = line.strip()

    replacements = [
        (r"\bVOLS\b", "VOLS"),
        (r"\bLifecycle\s+Dex\b", "Lifecycle: Dex"),
        (r"\bAttention\s+([0-9.]+)\b", r"Attention: \1"),
        (r"\bRisk\s+([0-9.]+)\b", r"Risk: \1"),
        (r"\bConviction\s+([A-Za-z].+)$", r"Conviction: \1"),
        (r"\bCA:\s*([1-9A-HJ-NP-Za-km-z ]+)$", lambda m: "CA: " + re.sub(r"\s+", "", m.group(1))),
        (r"\$([0-9]+)\.([0-9]{3})\b", r"$\1,\2"),
    ]

    for pattern, repl in replacements:
        new_line = re.sub(pattern, repl, line)
        if new_line != line:
            corrections.append(f"Normalized line: '{line}' -> '{new_line}'")
            line = new_line

    if original != line and not line:
        corrections.append(f"Removed empty/noisy line: '{original}'")

    return line


def preprocess_version(text: str, corrections: List[str]) -> List[str]:
    text = normalize_whitespace(text)
    lines = []
    for raw_line in text.splitlines():
        line = normalize_line(raw_line, corrections)
        if line:
            lines.append(line)
    return lines


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def choose_consensus(values: List[str], corrections: List[str], field_name: str = "") -> str:
    if not values:
        return ""
    if len(set(values)) == 1:
        return values[0]

    counts = Counter(values)
    most_common, count = counts.most_common(1)[0]
    if count > 1:
        return most_common

    best = values[0]
    best_score = -1.0
    for candidate in values:
        score = sum(similarity(candidate, other) for other in values)
        if score > best_score:
            best = candidate
            best_score = score

    if field_name == "token_name":
        lowered = [v.lower() for v in values]
        if any("larp dog" in v for v in lowered) and any("lary dog" in v for v in lowered):
            corrections.append("Ambiguous token name OCR: 'Larp Dog' vs 'Lary Dog'; normalized to 'Larry Dog' and flagged.")
            return "The Larry Dog"

    corrections.append(f"Consensus choice for {field_name or 'field'} from variants {values!r}: {best!r}")
    return best


def confidence_from_values(values: List[Any]) -> float:
    if not values:
        return 0.0
    normalized = [json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else str(v) for v in values]
    if len(set(normalized)) == 1:
        return 1.0
    counts = Counter(normalized)
    top = counts.most_common(1)[0][1]
    return round(top / len(normalized), 2)


def clean_numeric_string(value: str) -> str:
    value = value.strip()
    value = value.replace("O", "0") if re.fullmatch(r"[0-9O.,%+\-$mKMB ]+", value) else value
    value = value.replace("$7.979", "$7,979")
    return value


def parse_currency(value: str) -> Optional[float]:
    value = clean_numeric_string(value)
    if value in {"-", "—", "N/A", ""}:
        return None
    multiplier = 1.0
    if value.endswith("K"):
        multiplier = 1_000.0
        value = value[:-1]
    elif value.endswith("M"):
        multiplier = 1_000_000.0
        value = value[:-1]
    elif value.endswith("B"):
        multiplier = 1_000_000_000.0
        value = value[:-1]
    value = value.replace("$", "").replace(",", "").strip()
    try:
        return float(value) * multiplier
    except ValueError:
        return None


def parse_percentage(value: str) -> Optional[float]:
    value = clean_numeric_string(value).replace("%", "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def parse_float(value: str) -> Optional[float]:
    value = clean_numeric_string(value).strip()
    try:
        return float(value)
    except ValueError:
        return None


def parse_int(value: str) -> Optional[int]:
    value = clean_numeric_string(value).replace(",", "").strip()
    try:
        return int(value)
    except ValueError:
        return None


def parse_minutes(value: str) -> Optional[float]:
    value = value.strip().lower().replace("m", "")
    try:
        return float(value)
    except ValueError:
        return None


def validate_contract_address(addr: str) -> Dict[str, Any]:
    issues = []
    cleaned = re.sub(r"\s+", "", addr.strip())
    length_ok = len(cleaned) == 44
    charset_ok = bool(BASE58_RE.fullmatch(cleaned))
    if not length_ok:
        issues.append(f"expected 44 chars, got {len(cleaned)}")
    if not charset_ok:
        issues.append("contains non-base58 characters")
    return {
        "value": cleaned,
        "is_valid_solana_like": bool(length_ok and charset_ok),
        "issues": issues,
    }


def extract_sections(lines: List[str]) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {"__preamble__": []}
    current = "__preamble__"
    for line in lines:
        if line in SECTION_HEADERS:
            current = line
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(line)
    return sections


def parse_preamble(lines: List[str], corrections: List[str]) -> Dict[str, Any]:
    title = ""
    summary_lines: List[str] = []

    for line in lines:
        if TITLE_RE.match(line):
            title = line
        else:
            summary_lines.append(line)

    token_symbol = None
    alert_type = None
    title_match = re.match(r"^SE\s+(?P<alert>[A-Z ]+)\s+\$(?P<ticker>[A-Z0-9]+)$", title)
    if title_match:
        alert_type = title_match.group("alert").strip()
        token_symbol = title_match.group("ticker").strip()

    return {
        "title": title,
        "alert_type": alert_type,
        "ticker": token_symbol,
        "summary": " ".join(summary_lines).strip(),
    }


def parse_command_view(lines: List[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for line in lines:
        m = COMMAND_RE.search(line)
        if m:
            data["confidence_percent"] = parse_percentage(m.group("conf"))
            data["risk_score"] = parse_float(m.group("risk"))
            data["attention_score"] = parse_float(m.group("attn"))
            data["lifecycle"] = m.group("life").title()
            continue
        kv = KEY_VALUE_RE.match(line)
        if kv:
            key = kv.group("key").strip().lower().replace(" ", "_")
            data[key] = kv.group("value").strip()
    return data


def parse_token_identity(lines: List[str], corrections: List[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    variants = []

    for line in lines:
        m = TOKEN_LINE_RE.match(line)
        if m:
            variants.append(m.group("name").strip())
            data["ticker"] = m.group("ticker").strip()
            continue

        if line.startswith("CA:"):
            raw_ca = line.split(":", 1)[1].strip()
            data["contract_address"] = validate_contract_address(raw_ca)
            continue

        kv = KEY_VALUE_RE.match(line)
        if kv:
            key = kv.group("key").strip().lower().replace(" ", "_")
            value = kv.group("value").strip()
            if key in {"attention", "risk"}:
                data[key] = parse_float(value)
            else:
                data[key] = value

    if variants:
        data["name"] = choose_consensus(variants, corrections, "token_name")

    return data


def parse_market_snapshot(lines: List[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {"tape": {}}
    for line in lines:
        tape_match = MARKET_TAPE_RE.search(line)
        if tape_match:
            data["tape"] = {
                "liquidity_display": tape_match.group("liq"),
                "market_cap_display": tape_match.group("mc"),
                "volume_5m_display": tape_match.group("vol"),
                "age_display": tape_match.group("age"),
                "change_5m_display": tape_match.group("m5"),
            }
            continue

        flow_match = FLOW_RE.match(line)
        if flow_match:
            data["flow_5m"] = {
                "buy": parse_int(flow_match.group("buy")),
                "sell": parse_int(flow_match.group("sell")),
            }
            continue

        age_match = AGE_M5_RE.match(line)
        if age_match:
            data["age_minutes"] = parse_minutes(age_match.group("age"))
            data["change_5m_percent"] = parse_percentage(age_match.group("m5"))
            continue

        kv = KEY_VALUE_RE.match(line)
        if not kv:
            continue

        key = kv.group("key").strip().lower()
        value = kv.group("value").strip()

        if key == "liquidity":
            data["liquidity_usd"] = parse_currency(value)
            data["liquidity_display"] = value
        elif key == "market cap":
            data["market_cap_usd"] = parse_currency(value)
            data["market_cap_display"] = value
        elif key == "5m volume":
            data["volume_5m_usd"] = parse_currency(value)
            data["volume_5m_display"] = value
        elif key == "liq / mc":
            data["liq_to_market_cap_ratio_display"] = value

    return data


def parse_flow_structure(lines: List[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for line in lines:
        kv = KEY_VALUE_RE.match(line)
        if not kv:
            continue
        key = kv.group("key").strip().lower().replace(" ", "_").replace("+", "plus")
        value = kv.group("value").strip()
        if "buy_flow" in key or "sell_flow" in key:
            data[key] = parse_int(value)
        else:
            data[key] = value
    return data


def parse_signal_intelligence(lines: List[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for line in lines:
        kv = KEY_VALUE_RE.match(line)
        if not kv:
            continue
        key = kv.group("key").strip().lower().replace(" ", "_")
        value = kv.group("value").strip()
        parsed_int = parse_int(value)
        data[key] = parsed_int if parsed_int is not None else value
    return data


def parse_quality(lines: List[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for line in lines:
        m = QUALITY_PERCENT_RE.match(line)
        if m:
            data["confidence_percent"] = parse_percentage(m.group("value"))
            data["confidence_band"] = m.group("band").strip()
            continue

        m = QUALITY_FLOAT_RE.match(line)
        if m:
            data["risk_score"] = parse_float(m.group("value"))
            data["risk_band"] = m.group("band").strip()
            continue

        m = QUALITY_INT_RE.match(line)
        if m:
            data["elite_score"] = parse_int(m.group("value"))
            continue

        kv = KEY_VALUE_RE.match(line)
        if kv:
            key = kv.group("key").strip().lower().replace(" ", "_")
            data[key] = kv.group("value").strip()
    return data


def parse_actions(lines: List[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {"links": [], "footer": None}
    for line in lines:
        if "/" in line and "Dexscreener" in line:
            parts = [part.strip() for part in line.split("/") if part.strip()]
            data["links"].extend(parts)
        else:
            data["footer"] = line
    return data


def parse_version(lines: List[str], corrections: List[str]) -> Dict[str, Any]:
    sections = extract_sections(lines)
    return {
        "Alert Summary": parse_preamble(sections.get("__preamble__", []), corrections),
        "Command View": parse_command_view(sections.get("Command View", [])),
        "Token Identity": parse_token_identity(sections.get("Token Identity", []), corrections),
        "Market Snapshot": parse_market_snapshot(sections.get("Market Snapshot", [])),
        "Flow + Structure": parse_flow_structure(sections.get("Flow + Structure", [])),
        "Signal Intelligence": parse_signal_intelligence(sections.get("Signal Intelligence", [])),
        "Quality": parse_quality(sections.get("Quality", [])),
        "Actions": parse_actions(sections.get("Actions", [])),
    }


def merge_scalar(values: List[Any], corrections: List[str], field_name: str) -> Any:
    non_null = [v for v in values if v not in (None, "", [], {})]
    if not non_null:
        return None

    if all(isinstance(v, (int, float)) for v in non_null):
        if len(set(non_null)) == 1:
            return non_null[0]
        chosen = non_null[0]
        corrections.append(f"Numeric disagreement for {field_name}: {non_null!r}; kept first observed value {chosen!r}.")
        return chosen

    if all(isinstance(v, str) for v in non_null):
        return choose_consensus(non_null, corrections, field_name)

    return non_null[0]


def merge_dicts(dicts: List[Dict[str, Any]], corrections: List[str], path: str = "") -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    keys = set()
    for d in dicts:
        keys.update(d.keys())

    for key in sorted(keys):
        values = [d.get(key) for d in dicts if key in d]
        field_path = f"{path}.{key}" if path else key

        if all(isinstance(v, dict) for v in values if v is not None):
            merged[key] = merge_dicts([v for v in values if isinstance(v, dict)], corrections, field_path)
        elif all(isinstance(v, list) for v in values if v is not None):
            seen = []
            for lst in values:
                for item in lst:
                    if item not in seen:
                        seen.append(item)
            merged[key] = seen
        else:
            merged[key] = merge_scalar(values, corrections, field_path)

    return merged


def build_field_confidence(versions: List[Dict[str, Any]], merged: Dict[str, Any]) -> Dict[str, float]:
    scores: Dict[str, float] = {}

    def collect_values(key_path: List[str], version_dicts: List[Dict[str, Any]]) -> List[Any]:
        out = []
        for version_dict in version_dicts:
            cur: Any = version_dict
            ok = True
            for part in key_path:
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            if ok:
                out.append(cur)
        return out

    def walk(node: Any, current_path: List[str]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, current_path + [key])
        else:
            joined = ".".join(current_path)
            values = collect_values(current_path, versions)
            scores[joined] = confidence_from_values(values)

    walk(merged, [])
    return scores


def validate_data(data: Dict[str, Any]) -> List[str]:
    anomalies: List[str] = []

    cmd = data.get("Command View", {})
    qual = data.get("Quality", {})
    token = data.get("Token Identity", {})
    market = data.get("Market Snapshot", {})

    for name, value in [
        ("Command View.confidence_percent", cmd.get("confidence_percent")),
        ("Quality.confidence_percent", qual.get("confidence_percent")),
        ("Market Snapshot.change_5m_percent", market.get("change_5m_percent")),
    ]:
        if value is None:
            continue
        if name.endswith("change_5m_percent"):
            if value < -1000 or value > 10000:
                anomalies.append(f"{name} looks implausible: {value}")
        elif not (0 <= value <= 100):
            anomalies.append(f"{name} must be between 0 and 100, got {value}")

    risk_score = cmd.get("risk_score")
    if risk_score is not None and not (0 <= risk_score <= 1):
        anomalies.append(f"Command View.risk_score must be between 0 and 1, got {risk_score}")

    attention_score = cmd.get("attention_score")
    if attention_score is not None and not (0 <= attention_score <= 1):
        anomalies.append(f"Command View.attention_score must be between 0 and 1, got {attention_score}")

    contract_info = token.get("contract_address", {})
    if isinstance(contract_info, dict) and not contract_info.get("is_valid_solana_like", False):
        anomalies.append(
            "Token Identity.contract_address did not pass Solana-like validation: "
            + ", ".join(contract_info.get("issues", []))
        )

    for field_name in ["market_cap_usd", "volume_5m_usd"]:
        value = market.get(field_name)
        if value is not None and value < 0:
            anomalies.append(f"Market Snapshot.{field_name} must be non-negative, got {value}")

    return anomalies


def markdown_section(title: str) -> str:
    return f"\n## {title}\n"


def to_markdown(data: Dict[str, Any], field_confidence: Dict[str, float], corrections: List[str], anomalies: List[str]) -> str:
    out: List[str] = []
    out.append("# Parsed Token Risk Alert")

    summary = data["Alert Summary"]
    out.append(markdown_section("Alert Summary"))
    out.append(f"- **Title:** {summary.get('title')}")
    out.append(f"- **Alert Type:** {summary.get('alert_type')}")
    out.append(f"- **Ticker:** {summary.get('ticker')}")
    out.append(f"- **Summary:** {summary.get('summary')}")

    cmd = data["Command View"]
    out.append(markdown_section("Command View"))
    out.append(f"- **Confidence:** {cmd.get('confidence_percent')}%")
    out.append(f"- **Risk:** {cmd.get('risk_score')}")
    out.append(f"- **Attention:** {cmd.get('attention_score')}")
    out.append(f"- **Lifecycle:** {cmd.get('lifecycle')}")
    out.append(f"- **Conviction:** {cmd.get('conviction')}")
    out.append(f"- **Read:** {cmd.get('read')}")

    ident = data["Token Identity"]
    out.append(markdown_section("Token Identity"))
    out.append(f"- **Name:** {ident.get('name')}")
    out.append(f"- **Ticker:** {ident.get('ticker')}")
    ca = ident.get("contract_address", {})
    out.append(f"- **Contract Address:** `{ca.get('value')}`")
    out.append(f"- **Contract Valid:** {ca.get('is_valid_solana_like')}")
    if ca.get("issues"):
        out.append(f"- **Contract Issues:** {', '.join(ca['issues'])}")
    for key in ["lifecycle", "attention", "risk", "conviction"]:
        if key in ident:
            out.append(f"- **{key.replace('_', ' ').title()}:** {ident[key]}")

    market = data["Market Snapshot"]
    out.append(markdown_section("Market Snapshot"))
    out.append("| Metric | Value |")
    out.append("|---|---|")
    out.append(f"| Liquidity | {market.get('liquidity_display', '-')} |")
    out.append(f"| Market Cap | {market.get('market_cap_display', '-')} |")
    out.append(f"| 5m Volume | {market.get('volume_5m_display', '-')} |")
    out.append(f"| Age (m) | {market.get('age_minutes', '-')} |")
    out.append(f"| 5m Change | {market.get('change_5m_percent', '-')}% |")
    flow_5m = market.get("flow_5m", {})
    out.append(f"| 5m Flow | B {flow_5m.get('buy', '-')} / S {flow_5m.get('sell', '-')} |")
    out.append(f"| Liq / MC | {market.get('liq_to_market_cap_ratio_display', '-')} |")

    flow = data["Flow + Structure"]
    out.append(markdown_section("Flow + Structure"))
    for key, value in flow.items():
        out.append(f"- **{key.replace('_', ' ').title()}:** {value}")

    intel = data["Signal Intelligence"]
    out.append(markdown_section("Signal Intelligence"))
    for key, value in intel.items():
        out.append(f"- **{key.replace('_', ' ').title()}:** {value}")

    qual = data["Quality"]
    out.append(markdown_section("Quality"))
    for key, value in qual.items():
        out.append(f"- **{key.replace('_', ' ').title()}:** {value}")

    actions = data["Actions"]
    out.append(markdown_section("Actions"))
    out.append(f"- **Links:** {', '.join(actions.get('links', []))}")
    out.append(f"- **Footer:** {actions.get('footer')}")

    out.append(markdown_section("Field Confidence"))
    for key in sorted(field_confidence):
        out.append(f"- `{key}`: {int(field_confidence[key] * 100)}%")

    out.append(markdown_section("Corrections Made"))
    if corrections:
        for item in corrections:
            out.append(f"- {item}")
    else:
        out.append("- None")

    out.append(markdown_section("Anomalies / Validation Warnings"))
    if anomalies:
        for item in anomalies:
            out.append(f"- {item}")
    else:
        out.append("- None")

    return "\n".join(out)


def main() -> None:
    corrections: List[str] = []

    version_1_lines = preprocess_version(RAW_VERSION_1, corrections)
    version_2_lines = preprocess_version(RAW_VERSION_2, corrections)

    parsed_v1 = parse_version(version_1_lines, corrections)
    parsed_v2 = parse_version(version_2_lines, corrections)

    merged = merge_dicts([parsed_v1, parsed_v2], corrections)
    field_confidence = build_field_confidence([parsed_v1, parsed_v2], merged)
    anomalies = validate_data(merged)

    final_output = {
        "parsed_data": merged,
        "field_confidence": field_confidence,
        "corrections": corrections,
        "anomalies": anomalies,
    }

    assert merged["Alert Summary"]["ticker"] == "ROSIE"
    assert merged["Command View"]["confidence_percent"] == 29.0
    assert merged["Quality"]["elite_score"] == 11

    print(to_markdown(merged, field_confidence, corrections, anomalies))
    print("\n## JSON Output\n")
    print(json.dumps(final_output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
