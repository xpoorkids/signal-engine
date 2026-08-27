# Solana Trade Classification

Trade classification uses token balance deltas, quote-token balance deltas, program IDs, and instruction/log context.

Supported labels:

- `buy`
- `sell`
- `liquidity_add`
- `liquidity_remove`
- `transfer`
- `mint`
- `burn`
- `pool_initialization`
- `migration`
- `routing`
- `arbitrage`
- `unknown`

Wallet roles remain separate: fee payer, signer, trader, pool, router, buyer, seller, and creator are not assumed to be the same account.

Ambiguous routed or multi-swap transactions are retained with lower confidence and warnings instead of being forced into clean buy/sell labels.
