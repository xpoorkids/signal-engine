# Research Source Mode

Research commands use explicit modes:

- `source`: uses only configured source adapters and cached real responses.
- `fixture`: uses deterministic fixtures and never calls external APIs.
- `hybrid`: local development only; source and fixture rows remain separated.

Mutating commands require `--mode` or `SIGNAL_ENGINE_RESEARCH_MODE`. Source mode cannot call fixture builders. If source coverage is unavailable, commands return partial or blocked results instead of substituting generated data.

Source rows include `data_mode=source`; fixture rows include `data_mode=fixture`.

