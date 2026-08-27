# Matched Controls

Matched controls are selected using only pre-outcome variables.

## Allowed Matching Dimensions

- launchpad
- launch time bucket
- lifecycle stage
- initial liquidity bucket
- initial transaction bucket
- early volume bucket
- token age
- initial holder bucket
- early narrative category
- migration status

## Forbidden Variables

Future peak, future drawdown, later holder count, later liquidity, final winner labels, later KOL activity, and eventual social popularity cannot be used for matching.

The first implementation uses deterministic nearest pre-outcome buckets. Future work can add Mahalanobis or propensity-score matching only after leakage tests are in place.

