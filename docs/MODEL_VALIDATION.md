# Model Validation

Production decisions remain on `rules` until explicit operator approval.

## Modes

- `rules`
- `v2_shadow_rules`
- `shadow_ml`
- `approved_ml`

## Validation Rules

- Walk-forward only.
- Train only on samples resolved before the evaluation period.
- Group or guard splits by token, creator, funding cluster, wallet cluster, and time.
- Do not fetch present-day API data during historical replay and treat it as historical.
- Do not use future holder, wallet, creator, social, or outcome information for a past decision.

## Metrics

Track Brier score, log loss, reliability curves, calibration error, precision-recall AUC, top-bucket precision, major-runner recall, missed-runner rate, false-positive rate, coverage, net shadow P&L, median net return, maximum drawdown, and EV by score bucket.

No probability should be displayed as calibrated until comparable out-of-sample reliability supports it.

## Fee Feature Backtests

Backtest Fee Commitment and Fee Authenticity features in shadow against:

- target-before-stop success
- net executable return
- liquidity failure
- sell-route failure
- rug-like outcomes
- missed runners
- false-positive alerts

Do not deploy universal fee thresholds. Evaluate threshold candidates within token age, lifecycle stage, venue, liquidity, market cap, and market-regime buckets, then validate out of sample.
