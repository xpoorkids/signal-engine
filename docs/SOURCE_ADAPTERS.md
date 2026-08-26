# Source Adapters

Source adapters should use shared async clients with connection pooling, explicit timeouts, bounded concurrency, retry budgets, jittered backoff, circuit-breaker state, source-health telemetry, response validation, cache policy, and request correlation IDs.

## Current Status

- DexScreener remains useful for pool discovery, profiles, boosts, advertisements, and market context.
- DexScreener boosts and ads are paid visibility evidence, not organic demand.
- Jupiter currently uses the older quote path and remains a quote-only adapter.
- Helius listener exists but the V2 `transactionSubscribe` path has not been introduced yet.
- Birdeye is optional and must remain feature-flagged.

## Safety

Adapters must never submit transactions, request seed phrases, or require private keys. Jupiter V2 work is quote/order-construction only until a separate disabled live interface is explicitly approved.
