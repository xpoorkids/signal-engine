# Helius Backfill

The Helius adapter implements `getTransactionsForAddress` as the primary address-history source. It supports bounded page size, chronological sorting for replay, keyset cursor capture from the last returned signature, timestamp filtering, retry/budget handling through the shared research HTTP client, and raw provenance capture.

Standard RPC remains the fallback for account info, signatures, transactions, token supply, and largest accounts. Parsed Events support is not mandatory for this slice.

Normalized transactions preserve signature, slot, block time, success, error, fee payer, signers, account keys, network fee, token balances, native balances, logs, inner instructions, transaction version, parser version, request hash, response hash, and data mode.

