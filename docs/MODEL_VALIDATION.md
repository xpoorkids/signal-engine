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
# Manual Action Engine Shadow Validation

Manual action recommendations are persisted in `action_recommendations` for
shadow validation. They must not be described as proven profitable and must keep
`HEURISTIC_UNCALIBRATED` labels until evidence is sufficient.

Entry validation tracks return after 1 minute, 5 minutes, 15 minutes, and 1 hour;
maximum favorable excursion; maximum adverse excursion; target before
invalidation; executable net P&L; liquidity failure; sell-route failure; and
rug-like outcome.

Exit validation tracks return at recommendation, additional upside after the
recommendation, additional downside, whether the action sold too early, whether
it protected the position, whether a larger moon bag improved results, whether
principal recovery improved drawdown, catalyst invalidation timeliness, and
executable P&L under each exit policy.

Compare balanced, aggressive, and aggressive catalyst-runner profiles in shadow
before any production behavior changes. Operator approval is required before
profile or threshold changes become production behavior.
