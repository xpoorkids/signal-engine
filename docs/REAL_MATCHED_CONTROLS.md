# Real Matched Controls

Real controls are source-mode only and fixture controls are forbidden.

The control candidate pool must be built before outcomes are calculated and must use only pre-outcome fields:

- creation time
- launchpad
- token program
- early liquidity
- early market cap
- early trade count
- early unique buyers
- early net SOL flow
- early fee range
- migration status

Current implementation reports controls as blocked until historical creation windows are available from Helius, Solana RPC, or Birdeye. It does not substitute fixture controls.
