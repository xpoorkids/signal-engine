# Historical Fee Commitment

The research normalizer records directly observed transaction fees as `total_network_fee_lamports` and `total_network_fee_sol`.

Base and priority fee splits are populated only when compute-budget evidence and transaction metadata support the reconstruction. Otherwise:

- `base_fee_lamports = null`
- `priority_fee_lamports = null`
- `fee_split_status = unavailable`

Snapshot-level fee features include total fee SOL, successful fee SOL, failed fee SOL, fee-payer breadth, and fee rows. Organic fee authenticity remains unavailable when wallet-cluster coverage is incomplete.
