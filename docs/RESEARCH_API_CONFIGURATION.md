# Research API Configuration

Source-backed research uses these non-secret settings:

- `SIGNAL_ENGINE_RESEARCH_MODE=source`
- `SIGNAL_ENGINE_RESEARCH_DB_PATH=state/research.db`
- `SIGNAL_ENGINE_RESEARCH_DATA_DIR=research_data`
- `SIGNAL_ENGINE_RESEARCH_ARTIFACT_DIR=artifacts/research`
- `SIGNAL_ENGINE_RESEARCH_HTTP_TIMEOUT_SECONDS=30`
- `SIGNAL_ENGINE_RESEARCH_MAX_CONCURRENCY=3`
- `SIGNAL_ENGINE_RESEARCH_MAX_RETRIES=4`
- `SIGNAL_ENGINE_RESEARCH_REQUEST_BUDGET=1000`
- `SIGNAL_ENGINE_RESEARCH_MAX_PAGES_PER_JOB=1000`
- `SIGNAL_ENGINE_RESEARCH_RAW_CACHE_ENABLED=1`

Credentials are optional until running real source backfills:

- `HELIUS_API_KEY`
- `HELIUS_RPC_URL`
- `BIRDEYE_API_KEY`
- `JUPITER_API_KEY`

The CLI reports whether credentials are configured, unauthorized, plan-restricted, rate-limited, or unavailable. It never prints secret values.

