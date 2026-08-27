# Helius Full History

Source mode uses Helius `getTransactionsForAddress` as an RPC method at `https://mainnet.helius-rpc.com/?api-key=...`.

The collector requests:

- `transactionDetails=full`
- `sortOrder=asc`
- `encoding=jsonParsed`
- `maxSupportedTransactionVersion=0`
- `commitment=finalized`
- `filters.status=any`
- `filters.tokenAccounts=balanceChanged`

Pagination follows `paginationToken`. Each successful page persists the response hash in the research job metadata and raw cache. Stop states include `complete_to_requested_start`, `partial_request_budget`, `partial_page_limit`, `partial_record_limit`, `partial_source_error`, `empty`, and `unavailable`.

Legacy enhanced REST history is no longer the primary source path. If reintroduced later, it must be labeled as a fallback with `fallback_reason`.
