# Birdeye Backfill

Birdeye operations are endpoint-specific:

- creation info
- token overview
- OHLCV
- token trades
- holder distribution
- token security

Each operation reports its own status. A successful overview does not imply OHLCV, trades, holder history, security, fee history, or liquidity history are available.

OHLCV returned coverage is compared with requested coverage and marked partial when retention or endpoint limits prevent complete coverage. Current-only holder and security responses are not passed into historical replay.

