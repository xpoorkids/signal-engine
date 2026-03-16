from __future__ import annotations

import html
import json
import time
import uuid
from typing import Any

from app.services import signal_learning_service as sls
from worker import config as cfg

PROFILE_CONFIG_KEYS: tuple[str, ...] = (
    "CAND_MIN_TOKEN_AGE_SEC",
    "EARLY_ATTENTION_MIN",
    "EARLY_CREATOR_MIN",
    "PROM_MIN_LIQ_USD",
    "PROMOTION_MIN_ATTENTION",
    "PROMOTION_MAX_RISK",
    "PROMOTE_MIN_CONFIDENCE",
    "ATTENTION_CANDIDATE_THRESHOLD",
    "GATE_PROMOTE_MIN_LIQ",
    "GATE_PROMOTE_MIN_VOL5M",
    "GATE_PROMOTE_MIN_BUYS5M",
)

PROFILE_LABELS: dict[str, str] = {
    "strict": "Tighter promotion and market-quality requirements.",
    "balanced": "Current live baseline with no proposal overrides applied.",
    "aggressive": "Relaxed early filters for faster candidate capture.",
}
APPROVAL_STATUSES: set[str] = {"pending", "approved", "rolled_out", "rejected"}


def _proposal(
    *,
    reason: str,
    action: str,
    config_key: str,
    current_value: Any,
    proposed_value: Any,
    confidence: str,
    rationale: str,
    sample_size: int,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "action": action,
        "config_key": config_key,
        "current_value": current_value,
        "proposed_value": proposed_value,
        "confidence": confidence,
        "sample_size": sample_size,
        "rationale": rationale,
    }


def _format_env_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _format_diff_value(value: Any) -> str:
    return _format_env_value(value)


def _profile_baseline() -> dict[str, Any]:
    return {key: getattr(cfg, key) for key in PROFILE_CONFIG_KEYS}


def _approval_matches(
    approval: dict[str, Any],
    *,
    approval_kind: str | None = None,
    artifact_kind: str | None = None,
    target_name: str | None = None,
    rollout_status: str | None = None,
    query: str | None = None,
) -> bool:
    if approval_kind and str(approval.get("approval_kind") or "").lower() != approval_kind.lower():
        return False
    if artifact_kind and str(approval.get("artifact_kind") or "").lower() != artifact_kind.lower():
        return False
    if target_name and str(approval.get("target_name") or "").lower() != target_name.lower():
        return False
    if rollout_status and str(approval.get("rollout_status") or "").lower() != rollout_status.lower():
        return False
    if query:
        needle = query.lower()
        haystack = " ".join(
            [
                str(approval.get("approval_id") or ""),
                str(approval.get("approved_by") or ""),
                str(approval.get("approval_kind") or ""),
                str(approval.get("target_name") or ""),
                str(approval.get("artifact_kind") or ""),
                str(approval.get("notes") or ""),
                str(approval.get("artifact_text") or ""),
            ]
        ).lower()
        if needle not in haystack:
            return False
    return True


def build_tuning_proposals(hours: int = 72) -> dict[str, Any]:
    summary = sls.get_diagnostics_summary(hours=max(1, hours))
    guidance = summary.get("threshold_guidance") if isinstance(summary.get("threshold_guidance"), list) else []
    proposals: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []

    current_map: dict[str, tuple[str, Any]] = {
        "attention<0.20": ("EARLY_ATTENTION_MIN", float(cfg.EARLY_ATTENTION_MIN)),
        "buyers_low": ("PROMOTION_MIN_ATTENTION", float(cfg.PROMOTION_MIN_ATTENTION)),
        "dex_gate:liq<12000.0": ("PROM_MIN_LIQ_USD", float(cfg.PROM_MIN_LIQ_USD)),
        "risk_high": ("PROMOTION_MAX_RISK", float(cfg.PROMOTION_MAX_RISK)),
        "age<30s": ("CAND_MIN_TOKEN_AGE_SEC", int(cfg.CAND_MIN_TOKEN_AGE_SEC)),
    }

    for item in guidance:
        reason = str(item.get("reason") or "")
        action = str(item.get("action") or "hold")
        confidence = str(item.get("confidence") or "low")
        sample_size = int(item.get("sample_size") or 0)
        if action not in {"relax_slightly", "tighten"}:
            deferred.append(
                {
                    "reason": reason,
                    "action": action,
                    "confidence": confidence,
                    "sample_size": sample_size,
                    "rationale": str(item.get("rationale") or ""),
                }
            )
            continue
        if reason not in current_map:
            deferred.append(
                {
                    "reason": reason,
                    "action": action,
                    "confidence": confidence,
                    "sample_size": sample_size,
                    "rationale": "No explicit config mapping exists for this blocker yet.",
                }
            )
            continue

        config_key, current_value = current_map[reason]
        proposed_value = current_value
        if config_key == "EARLY_ATTENTION_MIN":
            delta = 0.02
            proposed_value = round(max(0.05, current_value - delta), 2) if action == "relax_slightly" else round(min(0.5, current_value + delta), 2)
        elif config_key == "PROMOTION_MIN_ATTENTION":
            delta = 0.03
            proposed_value = round(max(0.25, current_value - delta), 2) if action == "relax_slightly" else round(min(0.9, current_value + delta), 2)
        elif config_key == "PROM_MIN_LIQ_USD":
            delta = 2000.0
            proposed_value = max(2000.0, current_value - delta) if action == "relax_slightly" else current_value + delta
        elif config_key == "PROMOTION_MAX_RISK":
            delta = 0.03
            proposed_value = round(min(0.9, current_value + delta), 2) if action == "relax_slightly" else round(max(0.2, current_value - delta), 2)
        elif config_key == "CAND_MIN_TOKEN_AGE_SEC":
            delta = 5
            proposed_value = max(5, current_value - delta) if action == "relax_slightly" else current_value + delta

        proposals.append(
            _proposal(
                reason=reason,
                action=action,
                config_key=config_key,
                current_value=current_value,
                proposed_value=proposed_value,
                confidence=confidence,
                sample_size=sample_size,
                rationale=str(item.get("rationale") or ""),
            )
        )

    proposals.sort(
        key=lambda item: (
            {"tighten": 0, "relax_slightly": 1}.get(str(item["action"]), 2),
            {"high": 0, "medium": 1, "low": 2}.get(str(item["confidence"]), 3),
            -int(item["sample_size"]),
            str(item["config_key"]),
        )
    )

    preset_overrides = {
        "strict": {},
        "balanced": {},
        "aggressive": {},
    }
    for item in proposals:
        key = str(item["config_key"])
        proposed = item["proposed_value"]
        current = item["current_value"]
        action = str(item["action"])
        if action == "tighten":
            preset_overrides["strict"][key] = proposed
            preset_overrides["balanced"].setdefault(key, current)
        elif action == "relax_slightly":
            preset_overrides["aggressive"][key] = proposed
            preset_overrides["balanced"].setdefault(key, current)

    return {
        "lookback_hours": hours,
        "generated_from": "diagnostics.threshold_guidance",
        "proposal_count": len(proposals),
        "proposals": proposals,
        "deferred": deferred[:10],
        "preset_overrides": preset_overrides,
    }


def build_tuning_profiles(hours: int = 72) -> dict[str, Any]:
    proposal_payload = build_tuning_proposals(hours=max(1, hours))
    overrides = proposal_payload.get("preset_overrides") if isinstance(proposal_payload.get("preset_overrides"), dict) else {}
    baseline = _profile_baseline()
    profiles: dict[str, dict[str, Any]] = {}
    profile_diffs: dict[str, list[dict[str, Any]]] = {}

    for profile_name in ("strict", "balanced", "aggressive"):
        profile_values = dict(baseline)
        profile_values.update(overrides.get(profile_name, {}) if isinstance(overrides.get(profile_name), dict) else {})
        profiles[profile_name] = profile_values
        profile_diffs[profile_name] = [
            {
                "config_key": key,
                "current_value": baseline[key],
                "proposed_value": profile_values[key],
            }
            for key in PROFILE_CONFIG_KEYS
            if profile_values.get(key) != baseline.get(key)
        ]

    return {
        "lookback_hours": proposal_payload.get("lookback_hours", hours),
        "base_profile": "balanced",
        "profile_keys": list(PROFILE_CONFIG_KEYS),
        "profiles": profiles,
        "profile_diffs": profile_diffs,
        "profile_labels": dict(PROFILE_LABELS),
        "proposal_count": proposal_payload.get("proposal_count", 0),
    }


def render_tuning_env_snippet(hours: int = 72) -> str:
    payload = build_tuning_proposals(hours=max(1, hours))
    proposals = payload.get("proposals") if isinstance(payload.get("proposals"), list) else []
    lines = [
        "# Signal Engine tuning proposal export",
        f"# Generated from the last {int(payload.get('lookback_hours') or hours)} hours of diagnostics",
        "# Apply manually after review. Nothing here is auto-applied.",
    ]
    if not proposals:
        lines.append("# No concrete proposal overrides available.")
        return "\n".join(lines)

    for item in proposals:
        key = str(item.get("config_key") or "").strip()
        if not key:
            continue
        reason = str(item.get("reason") or "unknown")
        action = str(item.get("action") or "hold")
        confidence = str(item.get("confidence") or "low")
        sample_size = int(item.get("sample_size") or 0)
        lines.append(f"# {reason} | {action} | {confidence} confidence | sample {sample_size}")
        lines.append(f"{key}={_format_env_value(item.get('proposed_value'))}")
    return "\n".join(lines)


def render_tuning_apply_diff(hours: int = 72) -> str:
    payload = build_tuning_proposals(hours=max(1, hours))
    proposals = payload.get("proposals") if isinstance(payload.get("proposals"), list) else []
    header = [
        "# Apply Manually",
        f"# Proposed config changes from the last {int(payload.get('lookback_hours') or hours)} hours",
    ]
    if not proposals:
        header.append("- No concrete proposal changes available.")
        return "\n".join(header)

    rows = []
    for item in proposals:
        rows.append(
            "- "
            f"{item.get('config_key')}: "
            f"{_format_diff_value(item.get('current_value'))} -> {_format_diff_value(item.get('proposed_value'))} "
            f"[{item.get('action')} | {item.get('confidence')} | {item.get('reason')}]"
        )
    return "\n".join(header + rows)


def render_profile_env_snippet(profile_name: str, hours: int = 72) -> str:
    payload = build_tuning_profiles(hours=max(1, hours))
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    profile = profiles.get(profile_name) if isinstance(profiles.get(profile_name), dict) else {}
    lines = [
        f"# Signal Engine {profile_name} profile",
        f"# {PROFILE_LABELS.get(profile_name, 'Generated config profile.')}",
        f"# Built from current config plus proposal overrides over the last {int(payload.get('lookback_hours') or hours)} hours",
    ]
    if not profile:
        lines.append("# Profile data unavailable.")
        return "\n".join(lines)
    for key in PROFILE_CONFIG_KEYS:
        if key in profile:
            lines.append(f"{key}={_format_env_value(profile[key])}")
    return "\n".join(lines)


def render_profile_apply_diff(profile_name: str, hours: int = 72) -> str:
    payload = build_tuning_profiles(hours=max(1, hours))
    diffs = payload.get("profile_diffs") if isinstance(payload.get("profile_diffs"), dict) else {}
    profile_diffs = diffs.get(profile_name) if isinstance(diffs.get(profile_name), list) else []
    header = [
        f"# {profile_name.title()} profile diff",
        "# Compared against the current balanced baseline",
    ]
    if not profile_diffs:
        header.append("- No config differences from baseline.")
        return "\n".join(header)
    rows = [
        f"- {item['config_key']}: {_format_diff_value(item['current_value'])} -> {_format_diff_value(item['proposed_value'])}"
        for item in profile_diffs
    ]
    return "\n".join(header + rows)


def render_tuning_proposals_html(hours: int = 72) -> str:
    payload = build_tuning_proposals(hours=max(1, hours))
    proposals = payload.get("proposals") if isinstance(payload.get("proposals"), list) else []
    deferred = payload.get("deferred") if isinstance(payload.get("deferred"), list) else []
    preset_overrides = payload.get("preset_overrides") if isinstance(payload.get("preset_overrides"), dict) else {}
    env_snippet = render_tuning_env_snippet(hours=max(1, hours))
    apply_diff = render_tuning_apply_diff(hours=max(1, hours))

    proposal_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('config_key') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('current_value') or ''))}</td>"
        f"<td>{html.escape(str(item.get('proposed_value') or ''))}</td>"
        f"<td>{html.escape(str(item.get('action') or 'hold'))}</td>"
        f"<td>{html.escape(str(item.get('confidence') or 'low'))}</td>"
        "</tr>"
        for item in proposals
    ) or "<tr><td colspan='6'>No concrete proposals available.</td></tr>"

    deferred_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('action') or 'hold'))}</td>"
        f"<td>{html.escape(str(item.get('confidence') or 'low'))}</td>"
        f"<td>{html.escape(str(item.get('rationale') or ''))}</td>"
        "</tr>"
        for item in deferred
    ) or "<tr><td colspan='4'>No deferred items.</td></tr>"

    preset_rows = ""
    for preset_name, overrides in preset_overrides.items():
        overrides = overrides if isinstance(overrides, dict) else {}
        body = "".join(
            f"<li><strong>{html.escape(str(key))}</strong>: {html.escape(str(value))}</li>"
            for key, value in sorted(overrides.items())
        ) or "<li>No overrides</li>"
        preset_rows += (
            '<div class="preset-card">'
            f"<h3>{html.escape(str(preset_name))}</h3>"
            f"<ul>{body}</ul>"
            "</div>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tuning Proposals</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --panel-2: rgba(18, 34, 52, 0.96);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #071018 0%, #09131c 100%);
    }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      margin-top: 18px;
    }}
    .preset-grid {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:16px; }}
    .export-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:16px; }}
    .preset-card {{
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
    }}
    pre {{
      margin: 0;
      padding: 16px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(6, 16, 24, 0.92);
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      color: #d7e4ef;
      font-size: 13px;
      line-height: 1.5;
    }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 14px; font-size: 15px; letter-spacing: .12em; color: var(--muted); text-transform: uppercase; }}
    h3 {{ margin: 0 0 10px; font-size: 18px; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    ul {{ margin: 0; padding-left: 18px; color: var(--muted); }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; vertical-align: top; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    @media (max-width: 1020px) {{
      .preset-grid {{ grid-template-columns: 1fr; }}
      .export-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Tuning Proposals</h1>
      <p>Review-only config suggestions generated from the last {int(payload.get("lookback_hours") or hours)} hours of diagnostics. Nothing here applies automatically.</p>
    </section>
    <section class="panel">
      <h2>Concrete Proposals</h2>
      <table>
        <thead><tr><th>Reason</th><th>Config</th><th>Current</th><th>Proposed</th><th>Action</th><th>Confidence</th></tr></thead>
        <tbody>{proposal_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Preset Overrides</h2>
      <div class="preset-grid">{preset_rows}</div>
    </section>
    <section class="panel">
      <h2>Operational Exports</h2>
      <div class="export-grid">
        <div>
          <h3>.env Snippet</h3>
          <pre>{html.escape(env_snippet)}</pre>
        </div>
        <div>
          <h3>Apply Manually Diff</h3>
          <pre>{html.escape(apply_diff)}</pre>
        </div>
      </div>
    </section>
    <section class="panel">
      <h2>Deferred</h2>
      <table>
        <thead><tr><th>Reason</th><th>Action</th><th>Confidence</th><th>Why Deferred</th></tr></thead>
        <tbody>{deferred_rows}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def render_tuning_profiles_html(hours: int = 72) -> str:
    payload = build_tuning_profiles(hours=max(1, hours))
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    profile_diffs = payload.get("profile_diffs") if isinstance(payload.get("profile_diffs"), dict) else {}

    cards = ""
    for profile_name in ("strict", "balanced", "aggressive"):
        env_snippet = render_profile_env_snippet(profile_name, hours=max(1, hours))
        diff_text = render_profile_apply_diff(profile_name, hours=max(1, hours))
        diff_count = len(profile_diffs.get(profile_name) or [])
        cards += (
            '<section class="panel">'
            f"<h2>{html.escape(profile_name.title())}</h2>"
            f"<p>{html.escape(PROFILE_LABELS.get(profile_name, 'Generated config profile.'))}</p>"
            f"<p><strong>Changed keys vs balanced:</strong> {diff_count}</p>"
            '<div class="export-grid">'
            f"<div><h3>.env Profile</h3><pre>{html.escape(env_snippet)}</pre></div>"
            f"<div><h3>Manual Diff</h3><pre>{html.escape(diff_text)}</pre></div>"
            "</div>"
            "</section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tuning Profiles</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #071018 0%, #09131c 100%);
    }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      margin-top: 18px;
    }}
    .export-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:16px; }}
    pre {{
      margin: 0;
      padding: 16px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(6, 16, 24, 0.92);
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      color: #d7e4ef;
      font-size: 13px;
      line-height: 1.5;
    }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 12px; font-size: 22px; }}
    h3 {{ margin: 0 0 10px; font-size: 18px; }}
    p {{ margin: 0 0 12px; color: var(--muted); line-height: 1.5; }}
    @media (max-width: 1020px) {{
      .export-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Tuning Profiles</h1>
      <p>Complete env-ready profile bundles generated from the current baseline and proposal overrides from the last {int(payload.get("lookback_hours") or hours)} hours.</p>
    </section>
    {cards}
  </div>
</body>
</html>"""


def create_tuning_approval(
    *,
    approval_kind: str,
    artifact_kind: str,
    hours: int = 72,
    target_name: str | None = None,
    approved_by: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    lookback_hours = max(1, int(hours))
    kind = str(approval_kind or "").strip().lower()
    artifact = str(artifact_kind or "").strip().lower()
    target = str(target_name or "").strip().lower() or None

    if kind not in {"proposal", "profile"}:
        raise ValueError("invalid_approval_kind")
    if artifact not in {"env", "diff"}:
        raise ValueError("invalid_artifact_kind")
    if kind == "profile" and target not in {"strict", "balanced", "aggressive"}:
        raise ValueError("invalid_profile_target")

    if kind == "proposal":
        artifact_text = render_tuning_env_snippet(lookback_hours) if artifact == "env" else render_tuning_apply_diff(lookback_hours)
        payload = build_tuning_proposals(lookback_hours)
    else:
        artifact_text = (
            render_profile_env_snippet(target or "balanced", lookback_hours)
            if artifact == "env"
            else render_profile_apply_diff(target or "balanced", lookback_hours)
        )
        payload = build_tuning_profiles(lookback_hours)

    record = {
        "approval_id": f"tune-{uuid.uuid4().hex[:12]}",
        "created_ts": int(time.time()),
        "approved_by": (approved_by or "unknown").strip() or "unknown",
        "approval_kind": kind,
        "target_name": target,
        "artifact_kind": artifact,
        "lookback_hours": lookback_hours,
        "rollout_status": "approved",
        "rolled_out_ts": None,
        "notes": (notes or "").strip(),
        "artifact_text": artifact_text,
        "payload": payload,
    }
    with sls._connect() as c:
        c.execute(
            """
            INSERT INTO tuning_approvals (
                approval_id, created_ts, approved_by, approval_kind, target_name,
                artifact_kind, lookback_hours, rollout_status, rolled_out_ts,
                notes, artifact_text, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["approval_id"],
                record["created_ts"],
                record["approved_by"],
                record["approval_kind"],
                record["target_name"],
                record["artifact_kind"],
                record["lookback_hours"],
                record["rollout_status"],
                record["rolled_out_ts"],
                record["notes"],
                record["artifact_text"],
                json.dumps(record["payload"]),
            ),
        )
    return record


def list_tuning_approvals(
    limit: int = 20,
    *,
    approval_kind: str | None = None,
    artifact_kind: str | None = None,
    target_name: str | None = None,
    rollout_status: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    with sls._connect() as c:
        rows = c.execute(
            """
            SELECT approval_id, created_ts, approved_by, approval_kind, target_name,
                   artifact_kind, lookback_hours, rollout_status, rolled_out_ts,
                   notes, artifact_text, payload_json
            FROM tuning_approvals
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    approvals: list[dict[str, Any]] = []
    for row in rows:
        approval = {
            "approval_id": row["approval_id"],
            "created_ts": row["created_ts"],
            "approved_by": row["approved_by"],
            "approval_kind": row["approval_kind"],
            "target_name": row["target_name"],
            "artifact_kind": row["artifact_kind"],
            "lookback_hours": row["lookback_hours"],
            "rollout_status": row["rollout_status"] or "approved",
            "rolled_out_ts": row["rolled_out_ts"],
            "notes": row["notes"] or "",
            "artifact_text": row["artifact_text"],
            "payload": json.loads(row["payload_json"]) if row["payload_json"] else {},
        }
        if _approval_matches(
            approval,
            approval_kind=approval_kind,
            artifact_kind=artifact_kind,
            target_name=target_name,
            rollout_status=rollout_status,
            query=query,
        ):
            approvals.append(approval)
        if len(approvals) >= max(1, int(limit)):
            break
    return approvals


def update_tuning_approval_status(
    approval_id: str,
    *,
    rollout_status: str,
    notes: str | None = None,
) -> dict[str, Any]:
    status = str(rollout_status or "").strip().lower()
    if status not in APPROVAL_STATUSES:
        raise ValueError("invalid_rollout_status")
    now_ts = int(time.time())
    note_text = (notes or "").strip()
    with sls._connect() as c:
        existing = c.execute(
            """
            SELECT approval_id, notes FROM tuning_approvals WHERE approval_id=?
            """,
            (approval_id,),
        ).fetchone()
        if not existing:
            raise KeyError("tuning_approval_not_found")
        merged_notes = existing["notes"] or ""
        if note_text:
            merged_notes = f"{merged_notes}\n{note_text}".strip() if merged_notes else note_text
        c.execute(
            """
            UPDATE tuning_approvals
            SET rollout_status=?, rolled_out_ts=?, notes=?
            WHERE approval_id=?
            """,
            (
                status,
                now_ts if status == "rolled_out" else None,
                merged_notes,
                approval_id,
            ),
        )
    for item in list_tuning_approvals(limit=100):
        if item["approval_id"] == approval_id:
            return item
    raise KeyError("tuning_approval_not_found")


def get_latest_tuning_approval(
    *,
    approval_kind: str,
    artifact_kind: str,
    target_name: str | None = None,
    rollout_status: str = "approved",
) -> dict[str, Any] | None:
    kind = str(approval_kind or "").strip().lower()
    artifact = str(artifact_kind or "").strip().lower()
    target = str(target_name or "").strip().lower() or None
    status = str(rollout_status or "approved").strip().lower()
    if kind not in {"proposal", "profile"}:
        raise ValueError("invalid_approval_kind")
    if artifact not in {"env", "diff"}:
        raise ValueError("invalid_artifact_kind")
    if status not in APPROVAL_STATUSES:
        raise ValueError("invalid_rollout_status")
    if kind == "profile" and target not in {"strict", "balanced", "aggressive"}:
        raise ValueError("invalid_profile_target")

    with sls._connect() as c:
        row = c.execute(
            """
            SELECT approval_id, created_ts, approved_by, approval_kind, target_name,
                   artifact_kind, lookback_hours, rollout_status, rolled_out_ts,
                   notes, artifact_text, payload_json
            FROM tuning_approvals
            WHERE approval_kind=? AND artifact_kind=? AND rollout_status=?
              AND (? IS NULL OR target_name=?)
            ORDER BY created_ts DESC
            LIMIT 1
            """,
            (kind, artifact, status, target, target),
        ).fetchone()
    if not row:
        return None
    return {
        "approval_id": row["approval_id"],
        "created_ts": row["created_ts"],
        "approved_by": row["approved_by"],
        "approval_kind": row["approval_kind"],
        "target_name": row["target_name"],
        "artifact_kind": row["artifact_kind"],
        "lookback_hours": row["lookback_hours"],
        "rollout_status": row["rollout_status"] or "approved",
        "rolled_out_ts": row["rolled_out_ts"],
        "notes": row["notes"] or "",
        "artifact_text": row["artifact_text"],
        "payload": json.loads(row["payload_json"]) if row["payload_json"] else {},
    }


def render_latest_tuning_bundle_artifact(
    *,
    artifact_kind: str = "env",
    rollout_status: str = "rolled_out",
) -> str:
    artifact = str(artifact_kind or "env").strip().lower()
    status = str(rollout_status or "rolled_out").strip().lower()
    if artifact not in {"env", "diff"}:
        raise ValueError("invalid_artifact_kind")
    if status not in APPROVAL_STATUSES:
        raise ValueError("invalid_rollout_status")

    sections = [
        "# Signal Engine latest approved bundle",
        f"# rollout_status={status}",
        f"# artifact_kind={artifact}",
    ]

    latest_items: list[tuple[str, dict[str, Any] | None]] = [
        ("proposal", get_latest_tuning_approval(approval_kind="proposal", artifact_kind=artifact, rollout_status=status)),
        ("strict", get_latest_tuning_approval(approval_kind="profile", target_name="strict", artifact_kind=artifact, rollout_status=status)),
        ("balanced", get_latest_tuning_approval(approval_kind="profile", target_name="balanced", artifact_kind=artifact, rollout_status=status)),
        ("aggressive", get_latest_tuning_approval(approval_kind="profile", target_name="aggressive", artifact_kind=artifact, rollout_status=status)),
    ]

    included = 0
    for name, approval in latest_items:
        if not approval:
            continue
        included += 1
        sections.append("")
        sections.append(f"# [{name}] approval_id={approval['approval_id']}")
        sections.append(str(approval.get("artifact_text") or "").strip())

    if included == 0:
        sections.append("# No matching approved artifacts found.")
    return "\n".join(sections).strip()


def get_config_drift_report(
    *,
    target_name: str,
    rollout_status: str = "rolled_out",
) -> dict[str, Any]:
    target = str(target_name or "").strip().lower()
    status = str(rollout_status or "rolled_out").strip().lower()
    if target not in {"strict", "balanced", "aggressive"}:
        raise ValueError("invalid_profile_target")
    if status not in APPROVAL_STATUSES:
        raise ValueError("invalid_rollout_status")

    latest = get_latest_tuning_approval(
        approval_kind="profile",
        target_name=target,
        artifact_kind="env",
        rollout_status=status,
    )
    runtime = _profile_baseline()
    if latest is None:
        return {
            "target_name": target,
            "rollout_status": status,
            "approval": None,
            "runtime": runtime,
            "drift": [],
            "drift_count": 0,
        }

    payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    expected = profiles.get(target) if isinstance(profiles.get(target), dict) else {}
    drift = []
    for key in PROFILE_CONFIG_KEYS:
        expected_value = expected.get(key)
        runtime_value = runtime.get(key)
        if expected_value != runtime_value:
            drift.append(
                {
                    "config_key": key,
                    "expected_value": expected_value,
                    "runtime_value": runtime_value,
                }
            )
    return {
        "target_name": target,
        "rollout_status": status,
        "approval": latest,
        "runtime": runtime,
        "drift": drift,
        "drift_count": len(drift),
    }


def render_tuning_approvals_html(
    limit: int = 20,
    *,
    approval_kind: str | None = None,
    artifact_kind: str | None = None,
    target_name: str | None = None,
    rollout_status: str | None = None,
    query: str | None = None,
) -> str:
    approvals = list_tuning_approvals(
        limit=max(1, limit),
        approval_kind=approval_kind,
        artifact_kind=artifact_kind,
        target_name=target_name,
        rollout_status=rollout_status,
        query=query,
    )
    drift_sections = "".join(
        '<section class="panel">'
        f"<h2>Config Drift / {html.escape(profile_name.title())}</h2>"
        + (
            "<p>No drift against the latest rolled-out profile.</p>"
            if drift_payload["drift_count"] == 0
            else "<pre>"
            + html.escape(
                "\n".join(
                    f"{item['config_key']}: expected {_format_diff_value(item['expected_value'])} | runtime {_format_diff_value(item['runtime_value'])}"
                    for item in drift_payload["drift"]
                )
            )
            + "</pre>"
        )
        + "</section>"
        for profile_name, drift_payload in (
            ("strict", get_config_drift_report(target_name="strict", rollout_status="rolled_out")),
            ("balanced", get_config_drift_report(target_name="balanced", rollout_status="rolled_out")),
            ("aggressive", get_config_drift_report(target_name="aggressive", rollout_status="rolled_out")),
        )
    )
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('approval_kind') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('target_name') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('artifact_kind') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('rollout_status') or 'approved'))}</td>"
        f"<td>{html.escape(str(item.get('approved_by') or 'unknown'))}</td>"
        f"<td>{int(item.get('lookback_hours') or 0)}</td>"
        f"<td>{html.escape(str(item.get('notes') or ''))}</td>"
        "</tr>"
        for item in approvals
    ) or "<tr><td colspan='7'>No tuning approvals recorded yet.</td></tr>"

    artifacts = "".join(
        '<section class="panel">'
        f"<h2>{html.escape(str(item.get('approval_kind') or 'unknown').title())} / {html.escape(str(item.get('target_name') or 'default'))}</h2>"
        f"<p><strong>Approved by:</strong> {html.escape(str(item.get('approved_by') or 'unknown'))} &nbsp; "
        f"<strong>Artifact:</strong> {html.escape(str(item.get('artifact_kind') or 'unknown'))} &nbsp; "
        f"<strong>Status:</strong> {html.escape(str(item.get('rollout_status') or 'approved'))}</p>"
        f"<pre>{html.escape(str(item.get('artifact_text') or ''))}</pre>"
        "</section>"
        for item in approvals[:5]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tuning Approvals</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--text); font-family: "Segoe UI", sans-serif; background: linear-gradient(180deg, #071018 0%, #09131c 100%); }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; margin-top: 18px; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; vertical-align: top; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    pre {{ margin: 0; padding: 16px; border-radius: 16px; border: 1px solid var(--line); background: rgba(6, 16, 24, 0.92); white-space: pre-wrap; word-break: break-word; }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; }}
    p {{ margin: 0 0 12px; color: var(--muted); line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Tuning Approvals</h1>
      <p>Persisted review decisions for proposal and profile artifacts. These records make rollout history explicit.</p>
      <p><strong>Filters:</strong> kind={html.escape(str(approval_kind or 'all'))}, artifact={html.escape(str(artifact_kind or 'all'))}, target={html.escape(str(target_name or 'all'))}, status={html.escape(str(rollout_status or 'all'))}, query={html.escape(str(query or 'none'))}</p>
    </section>
    <section class="panel">
      <h2>Approval Log</h2>
      <table>
        <thead><tr><th>Kind</th><th>Target</th><th>Artifact</th><th>Status</th><th>Approved By</th><th>Hours</th><th>Notes</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    {drift_sections}
    {artifacts}
  </div>
</body>
</html>"""
