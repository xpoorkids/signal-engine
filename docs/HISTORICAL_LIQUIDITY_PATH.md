# Historical Liquidity Path

Historical liquidity uses only source rows observed at or before the snapshot time.

Allowed evidence:

- direct historical liquidity endpoint rows
- liquidity fields returned with historical candles
- liquidity add/remove transaction reconstruction
- pool reserve reconstruction

Current DEX Screener liquidity is stored as current context and is not passed into historical replay.
