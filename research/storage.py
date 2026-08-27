from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.db_service import connect_sqlite
from research.config import ResearchConfig


class ResearchStore:
    def __init__(self, config: ResearchConfig):
        self.config = config

    def connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.config.db_path)

    def init_schema(self) -> None:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.config.artifact_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_tokens (
                    token_id TEXT PRIMARY KEY,
                    supplied_address TEXT,
                    canonical_chain TEXT NOT NULL,
                    canonical_address TEXT NOT NULL,
                    symbol TEXT,
                    name TEXT,
                    source_label TEXT,
                    operator_outcome_label TEXT,
                    verification_status TEXT,
                    validation_status TEXT,
                    creation_ts INTEGER,
                    launchpad TEXT,
                    traded_status TEXT,
                    metadata_json TEXT NOT NULL,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_research_tokens_chain_addr ON research_tokens(canonical_chain, canonical_address);

                CREATE TABLE IF NOT EXISTS research_source_capabilities (
                    source TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    checked_ts INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_raw_fetches (
                    fetch_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    token_id TEXT,
                    wallet TEXT,
                    requested_start_ts INTEGER,
                    requested_end_ts INTEGER,
                    response_start_ts INTEGER,
                    response_end_ts INTEGER,
                    fetched_ts INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    response_hash TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    completeness_status TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_research_raw_fetches_hash ON research_raw_fetches(response_hash, fetched_ts);

                CREATE TABLE IF NOT EXISTS research_jobs (
                    job_id TEXT PRIMARY KEY,
                    cohort TEXT,
                    token_id TEXT,
                    source TEXT,
                    stage TEXT,
                    requested_start_ts INTEGER,
                    requested_end_ts INTEGER,
                    completed_start_ts INTEGER,
                    completed_end_ts INTEGER,
                    next_cursor TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    rate_limit_reset_ts INTEGER,
                    records_written INTEGER NOT NULL DEFAULT 0,
                    raw_response_hashes_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    updated_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_jobs_status ON research_jobs(status, source, stage);

                CREATE TABLE IF NOT EXISTS research_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    token_id TEXT NOT NULL,
                    snapshot_ts INTEGER NOT NULL,
                    snapshot_label TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    quality_json TEXT NOT NULL,
                    source_hashes_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_research_snapshots_token_ts_label ON research_snapshots(token_id, snapshot_ts, snapshot_label);

                CREATE TABLE IF NOT EXISTS research_outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    token_id TEXT NOT NULL,
                    snapshot_id TEXT,
                    labels_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    resolution_status TEXT NOT NULL,
                    outcome_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_outcomes_token ON research_outcomes(token_id);

                CREATE TABLE IF NOT EXISTS research_matches (
                    match_id TEXT PRIMARY KEY,
                    winner_token_id TEXT NOT NULL,
                    control_token_id TEXT NOT NULL,
                    matching_version TEXT NOT NULL,
                    matching_variables_json TEXT NOT NULL,
                    match_distance REAL NOT NULL,
                    reason_selected TEXT NOT NULL,
                    rejected_alternatives_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_research_matches_pair ON research_matches(winner_token_id, control_token_id, matching_version);

                CREATE TABLE IF NOT EXISTS research_action_replays (
                    replay_id TEXT PRIMARY KEY,
                    token_id TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    intended_size_usd REAL NOT NULL,
                    actions_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    replay_version TEXT NOT NULL,
                    created_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_replays_token ON research_action_replays(token_id, profile);
                """
            )
            self._ensure_column(conn, "research_tokens", "data_mode", "TEXT NOT NULL DEFAULT 'source'")
            self._ensure_column(conn, "research_raw_fetches", "data_mode", "TEXT NOT NULL DEFAULT 'source'")
            self._ensure_column(conn, "research_jobs", "data_mode", "TEXT NOT NULL DEFAULT 'source'")
            self._ensure_column(conn, "research_snapshots", "data_mode", "TEXT NOT NULL DEFAULT 'source'")
            self._ensure_column(conn, "research_outcomes", "data_mode", "TEXT NOT NULL DEFAULT 'source'")
            self._ensure_column(conn, "research_matches", "data_mode", "TEXT NOT NULL DEFAULT 'source'")
            self._ensure_column(conn, "research_action_replays", "data_mode", "TEXT NOT NULL DEFAULT 'source'")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_parquet_files (
                    file_id TEXT PRIMARY KEY,
                    table_name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    chain TEXT,
                    token TEXT,
                    data_mode TEXT NOT NULL,
                    schema_json TEXT NOT NULL,
                    created_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_parquet_table ON research_parquet_files(table_name, data_mode);
                """
            )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
