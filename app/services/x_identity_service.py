from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.services.db_service import connect_sqlite, resolve_engine_db_path


SEED_PATH = Path("config/operator_x_identity_blocklist.yaml")
PARSER_VERSION = "x-identity-risk-v1"
SEED_NAME = "operator_x_identity_blocklist"

AUTHORITATIVE_LINK_TYPES = {
    "official_token_social",
    "developer_profile",
    "creator_profile",
    "CTO_profile",
    "metadata_social",
    "DexScreener_social",
    "launchpad_social",
    "website_social",
    "Telegram_shared_profile",
    "profile_promoted_token",
    "operator_supplied",
}
EXPOSURE_ONLY_LINK_TYPES = {"repost_only", "mention_only"}
VERIFIED_LINEAGE_METHODS = {
    "stable_id_verified",
    "verified_rename_history",
    "operator_verified_alias_lineage",
    "manual_verified_alias_lineage",
}


class StableXUserIdConflict(ValueError):
    def __init__(self, stable_x_user_id: str, existing_identity_id: str):
        super().__init__("stable_x_user_id_conflict")
        self.stable_x_user_id = stable_x_user_id
        self.existing_identity_id = existing_identity_id


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def normalize_x_handle(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://") or "x.com/" in raw.lower() or "twitter.com/" in raw.lower():
        raw = normalize_x_profile_url(raw) or raw
    raw = raw.strip().lstrip("@").split("/")[0].split("?")[0].strip()
    if not raw:
        return None
    return raw.lower()


def display_handle(value: str | None) -> str | None:
    normalized = normalize_x_handle(value)
    return f"@{normalized}" if normalized else None


def normalize_x_profile_url(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        return normalize_x_handle(raw)
    parsed = urlparse(raw)
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "mobile.twitter.com":
        host = "twitter.com"
    if host not in {"x.com", "twitter.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    if parts[0].lower() in {"i", "intent", "share", "home", "search"}:
        return None
    return normalize_x_handle(parts[0])


def normalize_stable_x_user_id(value: str | None) -> str | None:
    stable = str(value or "").strip()
    if not stable:
        return None
    if not stable.isdigit() or not (3 <= len(stable) <= 30):
        raise ValueError("invalid_stable_x_user_id")
    return stable


def seed_content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def extract_official_x_identity_links(*sources: dict[str, Any] | None) -> list[dict[str, Any]]:
    extracted: dict[tuple[str, str], dict[str, Any]] = {}

    def add_link(raw: Any, link_type: str, source_name: str, metadata: dict[str, Any] | None = None) -> None:
        if not raw:
            return
        profile_url = str(raw) if str(raw).startswith(("http://", "https://")) else None
        normalized = normalize_x_profile_url(profile_url) if profile_url else normalize_x_handle(str(raw))
        if not normalized:
            return
        key = (normalized, link_type)
        extracted[key] = {
            "handle": f"@{normalized}",
            "profile_url": f"https://x.com/{normalized}",
            "link_type": link_type,
            "source": source_name,
            "metadata": metadata or {},
        }

    def visit(value: Any, default_source: str, default_link_type: str) -> None:
        if not isinstance(value, dict):
            return
        for key, link_type in (
            ("twitter_url", default_link_type),
            ("x_url", default_link_type),
            ("twitter", default_link_type),
            ("x", default_link_type),
            ("twitter_handle", default_link_type),
            ("x_handle", default_link_type),
        ):
            if value.get(key):
                add_link(value.get(key), link_type, default_source, {"field": key})
        links = value.get("links") if isinstance(value.get("links"), dict) else {}
        for key, link_type in (("twitter_url", default_link_type), ("x_url", default_link_type), ("twitter", default_link_type), ("x", default_link_type)):
            if links.get(key):
                add_link(links.get(key), link_type, default_source, {"field": f"links.{key}"})
        socials = value.get("socials") if isinstance(value.get("socials"), list) else []
        for social in socials:
            if not isinstance(social, dict):
                continue
            social_type = str(social.get("type") or social.get("name") or "").strip().lower()
            if social_type in {"twitter", "x"}:
                add_link(social.get("url") or social.get("handle"), default_link_type, default_source, {"field": "socials", "type": social_type})
        info = value.get("info") if isinstance(value.get("info"), dict) else {}
        if info:
            visit(info, default_source, default_link_type)

    for source in sources:
        if not isinstance(source, dict):
            continue
        source_name = str(source.get("x_identity_source") or source.get("source") or "action_context")
        visit(source, source_name, str(source.get("x_link_type") or "metadata_social"))
        market = source.get("market") if isinstance(source.get("market"), dict) else None
        if market:
            visit(market, "market_context", "DexScreener_social")
        dex_summary = source.get("dex_summary") if isinstance(source.get("dex_summary"), dict) else None
        if dex_summary:
            visit(dex_summary, "dex_summary", "DexScreener_social")
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else None
        if metadata:
            visit(metadata, "token_metadata", "metadata_social")
        dex_info = source.get("info") if isinstance(source.get("info"), dict) else None
        if dex_info:
            visit(dex_info, "dexscreener_info", "DexScreener_social")
    return list(extracted.values())


@dataclass(frozen=True)
class XIdentityDecision:
    action: str | None
    reason: str | None
    blockers: list[str]
    warnings: list[str]
    review_flags: list[str]
    matched_identity_id: str | None = None
    matched_lineage: str | None = None
    current_handle: str | None = None
    stable_x_user_id: str | None = None
    match_method: str | None = None
    link_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "review_flags": self.review_flags,
            "matched_identity_id": self.matched_identity_id,
            "matched_lineage": self.matched_lineage,
            "current_x_handle": self.current_handle,
            "stable_x_user_id": self.stable_x_user_id,
            "match_method": self.match_method,
            "link_type": self.link_type,
            "parser_version": PARSER_VERSION,
        }


class XIdentityService:
    def __init__(self, db_path: Path | str | None = None, *, seed_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else resolve_engine_db_path()
        self.seed_path = Path(seed_path) if seed_path is not None else SEED_PATH

    def _connect(self):
        return connect_sqlite(self.db_path)

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS x_identities (
                    identity_id TEXT PRIMARY KEY,
                    stable_x_user_id TEXT,
                    current_handle TEXT,
                    normalized_current_handle TEXT,
                    identity_confidence TEXT NOT NULL,
                    notes TEXT,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_x_identities_stable_id ON x_identities(stable_x_user_id) WHERE stable_x_user_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_x_identities_handle ON x_identities(normalized_current_handle);

                CREATE TABLE IF NOT EXISTS x_identity_aliases (
                    alias_id TEXT PRIMARY KEY,
                    identity_id TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    normalized_handle TEXT NOT NULL,
                    first_observed_ts INTEGER,
                    last_observed_ts INTEGER,
                    source TEXT NOT NULL,
                    evidence_ts INTEGER,
                    evidence_json TEXT NOT NULL,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_x_identity_alias_unique ON x_identity_aliases(identity_id, normalized_handle);
                CREATE INDEX IF NOT EXISTS idx_x_identity_alias_handle ON x_identity_aliases(normalized_handle);

                CREATE TABLE IF NOT EXISTS x_identity_blocks (
                    block_id TEXT PRIMARY KEY,
                    identity_id TEXT NOT NULL UNIQUE,
                    operator_block_status TEXT NOT NULL,
                    operator_block_reason TEXT NOT NULL,
                    disabled_ts INTEGER,
                    restored_ts INTEGER,
                    notes TEXT,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_x_identity_blocks_status ON x_identity_blocks(operator_block_status);

                CREATE TABLE IF NOT EXISTS x_identity_token_links (
                    link_id TEXT PRIMARY KEY,
                    token TEXT NOT NULL,
                    identity_id TEXT,
                    stable_x_user_id TEXT,
                    handle TEXT,
                    normalized_handle TEXT,
                    profile_url TEXT,
                    link_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    evidence_ts INTEGER,
                    identity_confidence TEXT NOT NULL,
                    match_method TEXT,
                    notes TEXT,
                    metadata_json TEXT NOT NULL,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_x_identity_token_links_token ON x_identity_token_links(token);
                CREATE INDEX IF NOT EXISTS idx_x_identity_token_links_identity ON x_identity_token_links(identity_id);
                CREATE INDEX IF NOT EXISTS idx_x_identity_token_links_handle ON x_identity_token_links(normalized_handle);

                CREATE TABLE IF NOT EXISTS x_identity_observations (
                    observation_id TEXT PRIMARY KEY,
                    identity_id TEXT,
                    token TEXT,
                    evidence_type TEXT NOT NULL,
                    observed_current_handle TEXT,
                    observed_aliases_json TEXT NOT NULL,
                    observed_rename_intervals_json TEXT NOT NULL,
                    evidence_ts INTEGER,
                    source TEXT NOT NULL,
                    operator_notes TEXT,
                    stable_x_user_id_status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_x_identity_observations_identity ON x_identity_observations(identity_id, evidence_ts);

                CREATE TABLE IF NOT EXISTS x_identity_seed_migrations (
                    seed_name TEXT NOT NULL,
                    seed_version TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    first_applied_ts INTEGER NOT NULL,
                    last_checked_ts INTEGER NOT NULL,
                    application_status TEXT NOT NULL,
                    identities_created INTEGER NOT NULL DEFAULT 0,
                    aliases_created INTEGER NOT NULL DEFAULT 0,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (seed_name, seed_version)
                );

                CREATE TABLE IF NOT EXISTS x_identity_audit_log (
                    audit_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    identity_id TEXT,
                    token TEXT,
                    actor_type TEXT NOT NULL,
                    actor_fingerprint TEXT,
                    request_id TEXT,
                    reason TEXT,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    error_type TEXT,
                    created_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_x_identity_audit_identity ON x_identity_audit_log(identity_id, created_ts);
                CREATE INDEX IF NOT EXISTS idx_x_identity_audit_action ON x_identity_audit_log(action, created_ts);
                """
            )

    def initialize_seed_blocklist(self, *, force_restore: bool = False, actor_fingerprint: str | None = None, request_id: str | None = None) -> dict[str, Any]:
        self.init_schema()
        if not self.seed_path.exists():
            return {"seed_path": str(self.seed_path), "identities": 0, "status": "missing_seed"}
        payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
        seed_version = str(payload.get("version") or "operator-x-identity-blocklist-v1")
        content_hash = seed_content_hash(payload)
        now = _now()
        identities_created = 0
        aliases_created = 0
        warnings: list[str] = []
        status = "applied"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing_migration = conn.execute(
                "SELECT * FROM x_identity_seed_migrations WHERE seed_name=? AND seed_version=?",
                (SEED_NAME, seed_version),
            ).fetchone()
            if existing_migration and not force_restore:
                conn.execute(
                    "UPDATE x_identity_seed_migrations SET last_checked_ts=?, application_status='already_applied' WHERE seed_name=? AND seed_version=?",
                    (now, SEED_NAME, seed_version),
                )
                self._audit_in_tx(conn, "seed_skipped", None, None, "system", actor_fingerprint, request_id, "same_seed_version_already_applied", dict(existing_migration), {"seed_version": seed_version, "content_hash": content_hash}, True)
                conn.commit()
                return {
                    "seed_path": str(self.seed_path),
                    "seed_name": SEED_NAME,
                    "seed_version": seed_version,
                    "content_hash": content_hash,
                    "identities_created": 0,
                    "aliases_created": 0,
                    "warnings": [],
                    "status": "already_applied",
                }
            if existing_migration and force_restore:
                status = "force_restored"
                warnings.append("force_restore_requested")
            elif not existing_migration:
                prior_versions = conn.execute("SELECT seed_version FROM x_identity_seed_migrations WHERE seed_name=? ORDER BY first_applied_ts DESC", (SEED_NAME,)).fetchall()
                if prior_versions:
                    status = "version_upgraded"
            for item in payload.get("identities", []):
                identity_id = item["identity_id"]
                normalized_current = normalize_x_handle(item.get("current_handle"))
                stable_seed = normalize_stable_x_user_id(item.get("stable_x_user_id")) if item.get("stable_x_user_id") else None
                existing_identity = conn.execute("SELECT * FROM x_identities WHERE identity_id=?", (identity_id,)).fetchone()
                if not existing_identity:
                    conn.execute(
                        """
                        INSERT INTO x_identities (
                            identity_id, stable_x_user_id, current_handle, normalized_current_handle,
                            identity_confidence, notes, created_ts, updated_ts
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            identity_id,
                            stable_seed,
                            display_handle(item.get("current_handle")),
                            normalized_current,
                            item.get("identity_confidence") or "operator_supplied_alias_lineage",
                            item.get("notes"),
                            now,
                            now,
                        ),
                    )
                    identities_created += 1
                else:
                    if stable_seed and not existing_identity["stable_x_user_id"]:
                        conn.execute("UPDATE x_identities SET stable_x_user_id=COALESCE(stable_x_user_id, ?), updated_ts=? WHERE identity_id=?", (stable_seed, now, identity_id))
                    if normalized_current and not existing_identity["normalized_current_handle"]:
                        conn.execute("UPDATE x_identities SET current_handle=?, normalized_current_handle=?, updated_ts=? WHERE identity_id=?", (display_handle(item.get("current_handle")), normalized_current, now, identity_id))
                block = conn.execute("SELECT * FROM x_identity_blocks WHERE identity_id=?", (identity_id,)).fetchone()
                if not block:
                    conn.execute(
                        """
                        INSERT INTO x_identity_blocks (
                            block_id, identity_id, operator_block_status, operator_block_reason,
                            disabled_ts, restored_ts, notes, created_ts, updated_ts
                        ) VALUES (?, ?, 'active', ?, NULL, NULL, ?, ?, ?)
                        """,
                        (f"block:{identity_id}", identity_id, item.get("operator_block_reason") or item.get("operator_label") or "operator_blocked", item.get("notes"), now, now),
                    )
                elif force_restore:
                    conn.execute(
                        """
                        UPDATE x_identity_blocks
                        SET operator_block_status='active', restored_ts=?, updated_ts=?
                        WHERE identity_id=?
                        """,
                        (now, now, identity_id),
                    )
                if normalized_current:
                    aliases_created += self._upsert_alias_in_tx(conn, identity_id, display_handle(item.get("current_handle")) or f"@{normalized_current}", normalized_current, "operator_seed", now, {"seed_version": seed_version, "operator_label": item.get("operator_label"), "kind": "current_handle"})
                for alias in item.get("historical_aliases", []):
                    normalized_alias = normalize_x_handle(alias)
                    if normalized_alias:
                        aliases_created += self._upsert_alias_in_tx(conn, identity_id, f"@{normalized_alias}", normalized_alias, "operator_seed", now, {"seed_version": seed_version, "operator_label": item.get("operator_label")})
            conn.execute(
                """
                INSERT INTO x_identity_seed_migrations (
                    seed_name, seed_version, content_hash, first_applied_ts, last_checked_ts,
                    application_status, identities_created, aliases_created, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(seed_name, seed_version) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    last_checked_ts=excluded.last_checked_ts,
                    application_status=excluded.application_status,
                    identities_created=x_identity_seed_migrations.identities_created + excluded.identities_created,
                    aliases_created=x_identity_seed_migrations.aliases_created + excluded.aliases_created,
                    warnings_json=excluded.warnings_json
                """,
                (SEED_NAME, seed_version, content_hash, now, now, status, identities_created, aliases_created, _json(warnings)),
            )
            self._audit_in_tx(conn, "seed_applied" if status == "applied" else status, None, None, "system", actor_fingerprint, request_id, "operator_seed_sync", {}, {"seed_version": seed_version, "content_hash": content_hash, "force_restore": force_restore}, True)
            conn.commit()
        return {
            "seed_path": str(self.seed_path),
            "seed_name": SEED_NAME,
            "seed_version": seed_version,
            "content_hash": content_hash,
            "identities": identities_created,
            "identities_created": identities_created,
            "aliases_created": aliases_created,
            "warnings": warnings,
            "status": status,
        }

    def ensure_seeded_once(self) -> dict[str, Any]:
        self.init_schema()
        if not self.seed_path.exists():
            return {"status": "missing_seed"}
        payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
        seed_version = str(payload.get("version") or "operator-x-identity-blocklist-v1")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM x_identity_seed_migrations WHERE seed_name=? AND seed_version=?", (SEED_NAME, seed_version)).fetchone()
        if row:
            return {"status": "already_applied", "seed_version": seed_version, "content_hash": row["content_hash"]}
        return self.initialize_seed_blocklist()

    def add_blocked_identity(
        self,
        *,
        identity_id: str | None = None,
        current_handle: str | None = None,
        stable_x_user_id: str | None = None,
        identity_confidence: str = "operator_supplied",
        operator_block_reason: str = "operator_blocked",
        notes: str | None = None,
        actor_fingerprint: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self.init_schema()
        now = _now()
        normalized = normalize_x_handle(current_handle)
        stable_x_user_id = normalize_stable_x_user_id(stable_x_user_id) if stable_x_user_id else None
        identity_id = identity_id or self._identity_id(stable_x_user_id, normalized)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conflict = self._stable_conflict(conn, identity_id, stable_x_user_id)
            if conflict:
                self._audit_in_tx(
                    conn,
                    "stable_id_conflict",
                    identity_id,
                    None,
                    "operator",
                    actor_fingerprint,
                    request_id,
                    "stable_x_user_id_conflict",
                    {},
                    {"stable_x_user_id": stable_x_user_id, "existing_identity_id": conflict},
                    False,
                    error_type="stable_x_user_id_conflict",
                )
                conn.commit()
                raise StableXUserIdConflict(stable_x_user_id or "", conflict)
            before = conn.execute("SELECT * FROM x_identities WHERE identity_id=?", (identity_id,)).fetchone()
            conn.execute(
                """
                INSERT INTO x_identities (
                    identity_id, stable_x_user_id, current_handle, normalized_current_handle,
                    identity_confidence, notes, created_ts, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_id) DO UPDATE SET
                    stable_x_user_id=COALESCE(excluded.stable_x_user_id, x_identities.stable_x_user_id),
                    current_handle=COALESCE(excluded.current_handle, x_identities.current_handle),
                    normalized_current_handle=COALESCE(excluded.normalized_current_handle, x_identities.normalized_current_handle),
                    identity_confidence=excluded.identity_confidence,
                    notes=COALESCE(excluded.notes, x_identities.notes),
                    updated_ts=excluded.updated_ts
                """,
                (identity_id, stable_x_user_id, display_handle(current_handle), normalized, identity_confidence, notes, now, now),
            )
            block_id = f"block:{identity_id}"
            conn.execute(
                """
                INSERT INTO x_identity_blocks (
                    block_id, identity_id, operator_block_status, operator_block_reason,
                    disabled_ts, restored_ts, notes, created_ts, updated_ts
                ) VALUES (?, ?, 'active', ?, NULL, NULL, ?, ?, ?)
                ON CONFLICT(identity_id) DO UPDATE SET
                    operator_block_status='active',
                    operator_block_reason=excluded.operator_block_reason,
                    restored_ts=excluded.restored_ts,
                    notes=COALESCE(excluded.notes, x_identity_blocks.notes),
                    updated_ts=excluded.updated_ts
                """,
                (block_id, identity_id, operator_block_reason, notes, now, now),
            )
            if normalized:
                self._upsert_alias_in_tx(conn, identity_id, display_handle(current_handle) or f"@{normalized}", normalized, "current_handle", now, {"kind": "current_handle"})
            after = {"identity_id": identity_id, "stable_x_user_id": stable_x_user_id, "current_handle": display_handle(current_handle), "operator_block_status": "active"}
            self._audit_in_tx(conn, "identity_added", identity_id, None, "operator", actor_fingerprint, request_id, operator_block_reason, dict(before) if before else {}, after, True)
            conn.commit()
        return self.get_identity(identity_id) or {"identity_id": identity_id}

    def add_current_handle(
        self,
        identity_id: str,
        handle: str,
        *,
        source: str = "operator_manual",
        evidence_ts: int | None = None,
        actor_fingerprint: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self.init_schema()
        normalized = normalize_x_handle(handle)
        if not normalized:
            raise ValueError("valid_x_handle_required")
        now = _now()
        with self._connect() as conn:
            before = conn.execute("SELECT * FROM x_identities WHERE identity_id=?", (identity_id,)).fetchone()
            cur = conn.execute("UPDATE x_identities SET current_handle=?, normalized_current_handle=?, updated_ts=? WHERE identity_id=?", (f"@{normalized}", normalized, now, identity_id))
            if cur.rowcount == 0:
                raise KeyError(identity_id)
            self._upsert_alias_in_tx(conn, identity_id, f"@{normalized}", normalized, source, evidence_ts or now, {"kind": "current_handle"})
            self._audit_in_tx(conn, "current_handle_changed", identity_id, None, "operator", actor_fingerprint, request_id, source, dict(before) if before else {}, {"current_handle": f"@{normalized}"}, True)
        return self.get_identity(identity_id) or {"identity_id": identity_id}

    def add_stable_x_user_id(
        self,
        identity_id: str,
        stable_x_user_id: str,
        *,
        actor_fingerprint: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self.init_schema()
        stable = normalize_stable_x_user_id(stable_x_user_id)
        if not stable:
            raise ValueError("stable_x_user_id_required")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            before = conn.execute("SELECT * FROM x_identities WHERE identity_id=?", (identity_id,)).fetchone()
            if not before:
                raise KeyError(identity_id)
            conflict = self._stable_conflict(conn, identity_id, stable)
            if conflict:
                self._audit_in_tx(
                    conn,
                    "stable_id_conflict",
                    identity_id,
                    None,
                    "operator",
                    actor_fingerprint,
                    request_id,
                    "stable_x_user_id_conflict",
                    dict(before),
                    {"stable_x_user_id": stable, "existing_identity_id": conflict},
                    False,
                    error_type="stable_x_user_id_conflict",
                )
                conn.commit()
                raise StableXUserIdConflict(stable, conflict)
            cur = conn.execute("UPDATE x_identities SET stable_x_user_id=?, identity_confidence='stable_id_verified', updated_ts=? WHERE identity_id=?", (stable, _now(), identity_id))
            if cur.rowcount == 0:
                raise KeyError(identity_id)
            self._audit_in_tx(conn, "stable_id_added", identity_id, None, "operator", actor_fingerprint, request_id, "stable_x_user_id_verified", dict(before), {"stable_x_user_id": stable}, True)
            conn.commit()
        return self.get_identity(identity_id) or {"identity_id": identity_id}

    def add_historical_alias(
        self,
        identity_id: str,
        handle: str,
        *,
        first_observed_ts: int | None = None,
        last_observed_ts: int | None = None,
        source: str = "operator_manual",
        evidence_ts: int | None = None,
        evidence: dict[str, Any] | None = None,
        actor_fingerprint: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self.init_schema()
        normalized = normalize_x_handle(handle)
        if not normalized:
            raise ValueError("valid_x_handle_required")
        now = _now()
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM x_identities WHERE identity_id=?", (identity_id,)).fetchone()
            if not exists:
                raise KeyError(identity_id)
            created = self._upsert_alias_in_tx(
                conn,
                identity_id,
                f"@{normalized}",
                normalized,
                source,
                evidence_ts or now,
                evidence or {},
                first_observed_ts=first_observed_ts,
                last_observed_ts=last_observed_ts,
            )
            self._audit_in_tx(conn, "alias_added", identity_id, None, "operator", actor_fingerprint, request_id, source, {}, {"handle": f"@{normalized}", "created": bool(created)}, True)
        return self.get_identity(identity_id) or {"identity_id": identity_id}

    def disable_block(self, identity_id: str, *, notes: str | None = None, actor_fingerprint: str | None = None, request_id: str | None = None) -> dict[str, Any]:
        return self._set_block(identity_id, "disabled", notes=notes, actor_fingerprint=actor_fingerprint, request_id=request_id)

    def restore_block(self, identity_id: str, *, notes: str | None = None, actor_fingerprint: str | None = None, request_id: str | None = None) -> dict[str, Any]:
        return self._set_block(identity_id, "active", notes=notes, actor_fingerprint=actor_fingerprint, request_id=request_id)

    def list_blocked_identities(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        self.init_schema()
        where = "" if include_disabled else "WHERE b.operator_block_status='active'"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT i.*, b.operator_block_status, b.operator_block_reason, b.disabled_ts, b.restored_ts, b.notes AS block_notes
                FROM x_identities i JOIN x_identity_blocks b ON b.identity_id=i.identity_id
                {where}
                ORDER BY i.identity_id
                """
            ).fetchall()
        return [self._row_to_identity(row) for row in rows]

    def get_identity(self, identity_id: str) -> dict[str, Any] | None:
        self.init_schema()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT i.*, b.operator_block_status, b.operator_block_reason, b.disabled_ts, b.restored_ts, b.notes AS block_notes
                FROM x_identities i LEFT JOIN x_identity_blocks b ON b.identity_id=i.identity_id
                WHERE i.identity_id=?
                """,
                (identity_id,),
            ).fetchone()
        return self._row_to_identity(row) if row else None

    def link_token_identity(self, token: str, *, link_type: str, source: str, **kwargs: Any) -> dict[str, Any]:
        self.init_schema()
        token = str(token or "").strip()
        if not token:
            raise ValueError("token_required")
        handle = kwargs.get("handle") or kwargs.get("current_handle") or kwargs.get("x_handle")
        profile_url = kwargs.get("profile_url")
        normalized = normalize_x_handle(handle) or normalize_x_profile_url(profile_url)
        stable = normalize_stable_x_user_id(kwargs.get("stable_x_user_id")) if kwargs.get("stable_x_user_id") else None
        identity_id = kwargs.get("identity_id")
        if not identity_id:
            matched = self._find_active_block(stable=stable, normalized_handle=normalized)
            if matched and stable and matched.get("stable_x_user_id") and stable != matched.get("stable_x_user_id"):
                identity_id = None
                metadata = dict(kwargs.get("metadata") or {})
                metadata["handle_reuse_stable_id_conflict"] = True
                kwargs["metadata"] = metadata
            else:
                identity_id = matched.get("identity_id") if matched else None
        now = _now()
        link_id = kwargs.get("link_id") or self._link_id(token, link_type, stable, normalized, kwargs.get("evidence_ts"))
        metadata = dict(kwargs.get("metadata") or {})
        metadata["secrets_redacted"] = True
        actor_fingerprint = kwargs.get("actor_fingerprint")
        request_id = kwargs.get("request_id")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO x_identity_token_links (
                    link_id, token, identity_id, stable_x_user_id, handle, normalized_handle,
                    profile_url, link_type, source, evidence_ts, identity_confidence, match_method,
                    notes, metadata_json, created_ts, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    token,
                    identity_id,
                    stable,
                    f"@{normalized}" if normalized else None,
                    normalized,
                    self._safe_profile_url(profile_url),
                    link_type,
                    source,
                    kwargs.get("evidence_ts"),
                    kwargs.get("identity_confidence") or "unresolved",
                    kwargs.get("match_method"),
                    kwargs.get("notes"),
                    _json(metadata),
                    now,
                    now,
                ),
            )
            self._audit_in_tx(conn, "token_link_added", identity_id, token, "operator", actor_fingerprint, request_id, source, {}, {"link_id": link_id, "link_type": link_type, "normalized_handle": normalized}, True)
        return self.get_token_link(link_id) or {"link_id": link_id}

    def get_token_link(self, link_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM x_identity_token_links WHERE link_id=?", (link_id,)).fetchone()
        return self._row_to_link(row) if row else None

    def list_token_links_for_identity(self, identity_id: str) -> list[dict[str, Any]]:
        self.init_schema()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM x_identity_token_links WHERE identity_id=? ORDER BY COALESCE(evidence_ts, created_ts)", (identity_id,)).fetchall()
        return [self._row_to_link(row) for row in rows]

    def add_observation(self, *, evidence_type: str, source: str, identity_id: str | None = None, token: str | None = None, **kwargs: Any) -> dict[str, Any]:
        self.init_schema()
        now = _now()
        observation_id = kwargs.get("observation_id") or uuid.uuid4().hex
        observed_aliases = [display_handle(item) for item in (kwargs.get("observed_aliases") or []) if normalize_x_handle(item)]
        actor_fingerprint = kwargs.get("actor_fingerprint")
        request_id = kwargs.get("request_id")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO x_identity_observations (
                    observation_id, identity_id, token, evidence_type, observed_current_handle,
                    observed_aliases_json, observed_rename_intervals_json, evidence_ts, source,
                    operator_notes, stable_x_user_id_status, metadata_json, created_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    identity_id,
                    token,
                    evidence_type,
                    display_handle(kwargs.get("observed_current_handle")),
                    _json(observed_aliases),
                    _json(kwargs.get("observed_rename_intervals") or []),
                    kwargs.get("evidence_ts") or now,
                    source,
                    kwargs.get("operator_notes"),
                    kwargs.get("stable_x_user_id_status") or "unresolved",
                    _json(kwargs.get("metadata") or {}),
                    now,
                ),
            )
            self._audit_in_tx(conn, "observation_added", identity_id, token, "operator", actor_fingerprint, request_id, source, {}, {"observation_id": observation_id, "evidence_type": evidence_type}, True)
        return {"observation_id": observation_id, "identity_id": identity_id, "evidence_type": evidence_type, "stable_x_user_id_status": kwargs.get("stable_x_user_id_status") or "unresolved"}

    def evaluate_token(self, token: str, links: list[dict[str, Any]] | None = None) -> XIdentityDecision:
        self.init_schema()
        candidate_links = list(links or [])
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM x_identity_token_links WHERE token=?", (token,)).fetchall()
        candidate_links.extend(self._row_to_link(row) for row in rows)
        if not candidate_links:
            return XIdentityDecision(None, None, [], [], [])
        best_review: XIdentityDecision | None = None
        for link in candidate_links:
            link_type = str(link.get("link_type") or "unknown")
            try:
                stable = normalize_stable_x_user_id(link.get("stable_x_user_id")) if link.get("stable_x_user_id") else None
            except ValueError:
                best_review = best_review or XIdentityDecision(None, None, [], [], ["X IDENTITY REVIEW REQUIRED"], current_handle=display_handle(link.get("handle")), match_method="invalid_stable_x_user_id", link_type=link_type)
                continue
            normalized = normalize_x_handle(link.get("handle") or link.get("current_handle") or link.get("x_handle")) or normalize_x_profile_url(link.get("profile_url"))
            match_method = str(link.get("match_method") or "").strip()
            if link.get("fuzzy_match") or link.get("display_name_match") or link.get("avatar_match"):
                best_review = best_review or XIdentityDecision(None, None, [], [], ["X IDENTITY REVIEW REQUIRED"], current_handle=display_handle(normalized), stable_x_user_id=stable, match_method="weak_similarity", link_type=link_type)
                continue
            if link_type in EXPOSURE_ONLY_LINK_TYPES:
                match = self._find_active_block(stable=stable, normalized_handle=normalized)
                if match:
                    best_review = best_review or XIdentityDecision(None, "BLOCKED IDENTITY PROMOTION EXPOSURE", [], ["BLOCKED IDENTITY PROMOTION EXPOSURE"], ["X IDENTITY REVIEW REQUIRED"], match["identity_id"], match["operator_block_reason"], display_handle(normalized), stable, "promotion_exposure", link_type)
                continue
            if link_type not in AUTHORITATIVE_LINK_TYPES:
                continue
            match = self._find_active_block(stable=stable, normalized_handle=normalized)
            if not match:
                continue
            if stable and match.get("stable_x_user_id") and stable != match.get("stable_x_user_id"):
                best_review = best_review or XIdentityDecision(None, None, [], [], ["X IDENTITY REVIEW REQUIRED"], match["identity_id"], match["operator_block_reason"], display_handle(normalized), stable, "handle_reuse_stable_id_conflict", link_type)
                continue
            current_handle = display_handle(normalized) or match.get("current_handle")
            if stable and stable == match.get("stable_x_user_id"):
                return XIdentityDecision("HARD FAIL", "OPERATOR-BLOCKED X DEV IDENTITY", ["operator_blocked_x_identity"], [], [], match["identity_id"], match["operator_block_reason"], current_handle, stable, "stable_x_user_id", link_type)
            if match_method in VERIFIED_LINEAGE_METHODS:
                return XIdentityDecision("HARD FAIL", "BLOCKED DEV IDENTITY LINEAGE", ["operator_blocked_x_identity", "blocked_x_rename_lineage"], [], [], match["identity_id"], match["operator_block_reason"], current_handle, stable, "verified_rename_history", link_type)
            return XIdentityDecision("AVOID", "POSSIBLE BLOCKED DEV IDENTITY", ["blocked_x_handle_match_unresolved", "stable_x_identity_unresolved"], [], ["manual_identity_review_required"], match["identity_id"], match["operator_block_reason"], current_handle, stable, "exact_handle_unresolved", link_type)
        return best_review or XIdentityDecision(None, None, [], [], [])

    def risk_summary(self, identity_id: str) -> dict[str, Any]:
        identity = self.get_identity(identity_id)
        if not identity:
            raise KeyError(identity_id)
        links = self.list_token_links_for_identity(identity_id)
        alias_count = len(identity.get("historical_aliases", []))
        labels = ["OPERATOR BLOCKED"] if identity.get("operator_block_status") == "active" else []
        coin_handles = [h for h in [identity.get("current_handle"), *identity.get("historical_aliases", [])] if h and any(term in h.lower() for term in ("coin", "cto", "pump"))]
        if alias_count >= 5:
            labels.append("RAPID HANDLE ROTATION")
            labels.append("REBRAND RISK")
        if len(links) >= 2:
            labels.append("MULTI-LAUNCH DEV")
        if len(coin_handles) >= 3:
            labels.append("REPEATED PROJECT HOPPER")
        if not labels:
            labels.append("INSUFFICIENT EVIDENCE")
        return {
            "identity_id": identity_id,
            "labels": labels,
            "handle_rename_count": alias_count,
            "project_themed_rename_count": len(coin_handles),
            "coin_themed_rename_count": len(coin_handles),
            "tokens_connected": len({link["token"] for link in links}),
            "launches_connected": len(links),
            "creator_wallets_connected": 0,
            "previous_token_outcomes": "unavailable_until_research_backfill",
            "percentage_connected_tokens_faded": None,
            "percentage_liquidity_failure": None,
            "percentage_creator_selling": None,
            "median_mfe": None,
            "median_mae": None,
            "median_token_lifetime": None,
            "notes": "Repeated renaming is a risk feature, not universal proof of fraud.",
        }

    def _set_block(
        self,
        identity_id: str,
        status: str,
        *,
        notes: str | None = None,
        actor_fingerprint: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self.init_schema()
        now = _now()
        with self._connect() as conn:
            before = conn.execute("SELECT * FROM x_identity_blocks WHERE identity_id=?", (identity_id,)).fetchone()
            cur = conn.execute(
                """
                UPDATE x_identity_blocks
                SET operator_block_status=?, disabled_ts=CASE WHEN ?='disabled' THEN ? ELSE disabled_ts END,
                    restored_ts=CASE WHEN ?='active' THEN ? ELSE restored_ts END,
                    notes=COALESCE(?, notes), updated_ts=?
                WHERE identity_id=?
                """,
                (status, status, now, status, now, notes, now, identity_id),
            )
            if cur.rowcount:
                self._audit_in_tx(
                    conn,
                    "block_disabled" if status == "disabled" else "block_restored",
                    identity_id,
                    None,
                    "operator",
                    actor_fingerprint,
                    request_id,
                    notes,
                    dict(before) if before else {},
                    {"operator_block_status": status},
                    True,
                )
        if cur.rowcount == 0:
            raise KeyError(identity_id)
        return self.get_identity(identity_id) or {"identity_id": identity_id}

    def _find_active_block(self, *, stable: str | None, normalized_handle: str | None) -> dict[str, Any] | None:
        with self._connect() as conn:
            if stable:
                row = conn.execute(
                    """
                    SELECT i.*, b.operator_block_status, b.operator_block_reason, b.disabled_ts, b.restored_ts, b.notes AS block_notes
                    FROM x_identities i JOIN x_identity_blocks b ON b.identity_id=i.identity_id
                    WHERE i.stable_x_user_id=? AND b.operator_block_status='active'
                    """,
                    (stable,),
                ).fetchone()
                if row:
                    return self._row_to_identity(row)
            if normalized_handle:
                row = conn.execute(
                    """
                    SELECT i.*, b.operator_block_status, b.operator_block_reason, b.disabled_ts, b.restored_ts, b.notes AS block_notes
                    FROM x_identities i JOIN x_identity_blocks b ON b.identity_id=i.identity_id
                    WHERE b.operator_block_status='active'
                      AND (
                        i.normalized_current_handle=?
                        OR EXISTS (
                            SELECT 1 FROM x_identity_aliases a
                            WHERE a.identity_id=i.identity_id AND a.normalized_handle=?
                        )
                      )
                    """,
                    (normalized_handle, normalized_handle),
                ).fetchone()
                if row:
                    return self._row_to_identity(row)
        return None

    def _resolve_identity_id(self, stable: str | None, normalized: str | None) -> str | None:
        match = self._find_active_block(stable=stable, normalized_handle=normalized)
        return match.get("identity_id") if match else None

    def _upsert_alias_in_tx(self, conn, identity_id: str, handle: str, normalized: str, source: str, evidence_ts: int | None, evidence: dict[str, Any], *, first_observed_ts: int | None = None, last_observed_ts: int | None = None) -> int:
        now = _now()
        alias_id = hashlib.sha256(f"{identity_id}:{normalized}".encode("utf-8")).hexdigest()[:32]
        exists = conn.execute("SELECT 1 FROM x_identity_aliases WHERE identity_id=? AND normalized_handle=?", (identity_id, normalized)).fetchone()
        conn.execute(
            """
            INSERT INTO x_identity_aliases (
                alias_id, identity_id, handle, normalized_handle, first_observed_ts,
                last_observed_ts, source, evidence_ts, evidence_json, created_ts, updated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(identity_id, normalized_handle) DO UPDATE SET
                handle=excluded.handle,
                first_observed_ts=COALESCE(x_identity_aliases.first_observed_ts, excluded.first_observed_ts),
                last_observed_ts=COALESCE(excluded.last_observed_ts, x_identity_aliases.last_observed_ts),
                source=excluded.source,
                evidence_ts=excluded.evidence_ts,
                evidence_json=excluded.evidence_json,
                updated_ts=excluded.updated_ts
            """,
            (alias_id, identity_id, handle, normalized, first_observed_ts, last_observed_ts, source, evidence_ts, _json(evidence), now, now),
        )
        return 0 if exists else 1

    def _stable_conflict(self, conn, identity_id: str, stable: str | None) -> str | None:
        if not stable:
            return None
        row = conn.execute("SELECT identity_id FROM x_identities WHERE stable_x_user_id=? AND identity_id<>?", (stable, identity_id)).fetchone()
        return row["identity_id"] if row else None

    def _audit_in_tx(
        self,
        conn,
        action: str,
        identity_id: str | None,
        token: str | None,
        actor_type: str,
        actor_fingerprint: str | None,
        request_id: str | None,
        reason: str | None,
        before: dict[str, Any],
        after: dict[str, Any],
        success: bool,
        *,
        error_type: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO x_identity_audit_log (
                audit_id, action, identity_id, token, actor_type, actor_fingerprint,
                request_id, reason, before_json, after_json, success, error_type, created_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, action, identity_id, token, actor_type, actor_fingerprint, request_id, reason, _json(before), _json(after), 1 if success else 0, error_type, _now()),
        )

    def audit_event(self, action: str, *, identity_id: str | None = None, token: str | None = None, actor_type: str = "operator", actor_fingerprint: str | None = None, request_id: str | None = None, reason: str | None = None, before: dict[str, Any] | None = None, after: dict[str, Any] | None = None, success: bool = True, error_type: str | None = None) -> None:
        self.init_schema()
        with self._connect() as conn:
            self._audit_in_tx(conn, action, identity_id, token, actor_type, actor_fingerprint, request_id, reason, before or {}, after or {}, success, error_type=error_type)

    def list_audit_log(self) -> list[dict[str, Any]]:
        self.init_schema()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM x_identity_audit_log ORDER BY created_ts, audit_id").fetchall()
        return [dict(row) for row in rows]

    def _row_to_identity(self, row) -> dict[str, Any]:
        item = dict(row)
        with self._connect() as conn:
            aliases = conn.execute("SELECT * FROM x_identity_aliases WHERE identity_id=? ORDER BY COALESCE(first_observed_ts, created_ts), normalized_handle", (item["identity_id"],)).fetchall()
        item["historical_aliases"] = [dict(alias)["handle"] for alias in aliases if dict(alias)["normalized_handle"] != item.get("normalized_current_handle")]
        item["aliases"] = [dict(alias) for alias in aliases]
        return item

    def _row_to_link(self, row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = _load_json(item.pop("metadata_json", None), {})
        return item

    @staticmethod
    def _identity_id(stable: str | None, normalized: str | None) -> str:
        raw = stable or normalized or uuid.uuid4().hex
        return "xid_" + hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _link_id(token: str, link_type: str, stable: str | None, normalized: str | None, evidence_ts: Any) -> str:
        raw = f"{token}:{link_type}:{stable or normalized}:{evidence_ts or ''}"
        return "xlink_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _safe_profile_url(value: str | None) -> str | None:
        normalized = normalize_x_profile_url(value)
        return f"https://x.com/{normalized}" if normalized else None
