# Solana Transaction Normalization

Transaction normalization keeps roles separate:

- fee payer
- signer
- buyer
- seller
- router or sponsor

The direct transaction fee is preserved as `total_network_fee_lamports`. Base fee and priority fee remain null with `fee_split_status=unavailable` unless a documented reconstruction is available.

Trade classification uses token and quote balance changes. Ambiguous transactions remain `unknown`, `transfer`, or another non-buy/sell side with warnings instead of being forced into a swap label.

