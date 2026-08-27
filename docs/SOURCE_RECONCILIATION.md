# Source Reconciliation

`transaction_source_reconciliation` compares Helius and standard RPC history where both are available.

Recorded fields include:

- signatures in both sources
- Helius-only signatures
- RPC-only signatures
- mismatched fees
- mismatched timestamps
- parsing disagreements
- canonical selection rule

The current canonical rule is conservative: retain source-specific rows and prefer direct agreement for fee and timestamp fields. Disagreement is evidence, not zero.
