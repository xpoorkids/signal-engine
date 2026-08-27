# Solana RPC Hydration

The RPC fallback first paginates `getSignaturesForAddress` using finalized commitment and `before`/`until`.

Every discovered signature is then hydrated with `getTransaction` using:

- object-form config
- `encoding=jsonParsed`
- `maxSupportedTransactionVersion=0`
- `commitment=finalized`

Hydration states are retained:

- `hydrated`
- `null_result`
- `source_unavailable`
- `rate_limited`
- `malformed_response`
- `failed`

Null or pruned transactions are not silently discarded. They remain in raw transaction output and job metadata so the coverage boundary is auditable.
