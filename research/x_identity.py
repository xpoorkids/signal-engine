from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from app.services.x_identity_service import normalize_x_handle
from research.config import ResearchConfig
from research.storage import ResearchStore


RESEARCH_X_IDENTITY_VERSION = "research-x-identity-links-v1"


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _link_id(token_id: str, identity_id: str | None, handle: str | None, link_type: str, evidence_ts: int) -> str:
    raw = f"{token_id}:{identity_id or ''}:{normalize_x_handle(handle) or ''}:{link_type}:{evidence_ts}:{RESEARCH_X_IDENTITY_VERSION}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def record_research_x_identity_link(
    config: ResearchConfig,
    *,
    token_id: str,
    chain: str,
    contract_address: str,
    link_type: str,
    source: str,
    evidence_ts: int,
    identity_id: str | None = None,
    stable_x_user_id: str | None = None,
    linked_handle: str | None = None,
    linked_handle_at_launch: str | None = None,
    launch_ts: int | None = None,
    creator_wallet: str | None = None,
    funding_cluster: str | None = None,
    identity_confidence: str = "unresolved",
    point_in_time_aliases: list[dict[str, Any]] | None = None,
    outcome_summary: dict[str, Any] | None = None,
    action_replay_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    now = _now()
    normalized = normalize_x_handle(linked_handle)
    link_id = _link_id(token_id, identity_id, linked_handle, link_type, evidence_ts)
    with store.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO research_x_identity_token_links (
                link_id, token_id, chain, contract_address, identity_id, stable_x_user_id,
                linked_handle, normalized_handle, linked_handle_at_launch, link_type, source,
                evidence_ts, launch_ts, creator_wallet, funding_cluster, identity_confidence,
                point_in_time_alias_json, outcome_summary_json, action_replay_summary_json,
                data_mode, created_ts, updated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link_id,
                token_id,
                chain,
                contract_address,
                identity_id,
                stable_x_user_id,
                f"@{normalized}" if normalized else linked_handle,
                normalized,
                linked_handle_at_launch,
                link_type,
                source,
                evidence_ts,
                launch_ts,
                creator_wallet,
                funding_cluster,
                identity_confidence,
                _json(point_in_time_aliases or []),
                _json(outcome_summary or {}),
                _json(action_replay_summary or {}),
                config.mode,
                now,
                now,
            ),
        )
    return {
        "link_id": link_id,
        "token_id": token_id,
        "identity_id": identity_id,
        "normalized_handle": normalized,
        "link_type": link_type,
        "evidence_ts": evidence_ts,
        "data_mode": config.mode,
    }


def list_research_x_identity_links(config: ResearchConfig, *, identity_id: str | None = None, token_id: str | None = None) -> list[dict[str, Any]]:
    store = ResearchStore(config)
    store.init_schema()
    clauses = ["data_mode=?"]
    params: list[Any] = [config.mode]
    if identity_id:
        clauses.append("identity_id=?")
        params.append(identity_id)
    if token_id:
        clauses.append("token_id=?")
        params.append(token_id)
    with store.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM research_x_identity_token_links WHERE {' AND '.join(clauses)} ORDER BY evidence_ts",
            tuple(params),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["point_in_time_aliases"] = json.loads(item.pop("point_in_time_alias_json") or "[]")
        item["outcome_summary"] = json.loads(item.pop("outcome_summary_json") or "{}")
        item["action_replay_summary"] = json.loads(item.pop("action_replay_summary_json") or "{}")
        result.append(item)
    return result
