# Metric Dictionary

All V2 metrics must use `MetricValue` and include status, unit, source names, observed time, computed time, age, observation window, completeness, confidence, reasons, feature version, and calibration status.

## V2 Formula Metrics

| Metric | Definition | Formula | Source | Unit | Window | Freshness | Missing behavior | Downstream use | Limitations | Calibration | Version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `order_flow_imbalance` | Bounded notional buy/sell pressure. | `(buy_notional - sell_notional) / max(buy_notional + sell_notional, epsilon)`, clamped to `[-1, 1]`. | On-chain trades or DEX transaction stream. | ratio | 5s, 10s, 30s, 60s, 3m, 5m, 15m | Source timestamp required. | `missing`, not `0`, when either side is unavailable. | Organic flow and manipulation assessment. | Requires reliable side and notional classification. | Uncalibrated formula input. | `signal_engine_v2_formulas@1` |
| `wallet_notional_hhi` | Notional concentration across wallets. | `sum(wallet_share ** 2)`. | Trade wallet attribution. | ratio | Same as flow window. | Source timestamp required. | `missing` when total notional <= 0. | Wallet/holder quality and manipulation assessment. | Wallet addresses are not always independent people. | Uncalibrated formula input. | `signal_engine_v2_formulas@1` |
| `wallet_trade_count_hhi` | Trade-count concentration across wallets. | `sum(wallet_trade_count_share ** 2)`. | Trade wallet attribution. | ratio | Same as flow window. | Source timestamp required. | `missing` when total trades <= 0. | Manipulation and buyer breadth. | Dust splitting can hide coordination. | Uncalibrated formula input. | `signal_engine_v2_formulas@1` |
| `wallet_entropy` | Distribution entropy across wallet activity. | `-sum(share * ln(share))`. | Trade wallet attribution. | nats | Same as flow window. | Source timestamp required. | `missing` when total <= 0. | Wallet independence evidence. | Not normalized by wallet count yet. | Uncalibrated formula input. | `signal_engine_v2_formulas@1` |
| `wallet_gini` | Inequality of wallet notional or holder balances. | Sorted Gini coefficient. | Trade notional or corrected holder balances. | ratio | Snapshot or flow window. | Source timestamp required. | `missing` when total <= 0. | Holder distribution and manipulation. | Must exclude pools/burns/programs for holder use. | Uncalibrated formula input. | `signal_engine_v2_formulas@1` |
| `safe_ratio` | Ratio that preserves missing denominator. | `numerator / denominator`; returns `missing`/`None` when denominator is zero or unavailable. | Any source. | ratio | Caller-defined. | Caller-defined. | No zero fallback. | Shared formulas. | Caller must set metric status. | Not a model output. | `signal_engine_v2_formulas@1` |

## Fee Commitment and Fee Authenticity

Fee metrics are shadow-only in `signal_engine_v2_fee_commitment@1`. Total fee SOL is never a standalone bullish signal or hard minimum. Fee activity can confirm organic demand only when independent, non-creator-funded, non-bot, non-dust, and aligned with genuine buy growth, holder growth, liquidity, and execution quality.

| Metric | Definition | Formula | Source | Unit | Window | Freshness | Missing behavior | Downstream use | Limitations | Calibration | Version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `total_fee_sol` | All observed fee categories for the token window. | `network_fee_sol + priority_fee_sol + protocol_trading_fee_sol + creator_fee_generated_sol + creator_fee_claimed_sol`. | Solana transactions, launchpad/DEX fee evidence. | SOL | 5s..15m target windows | Transaction timestamp required. | `missing` only when no fee observations are available upstream. | Shadow feature only; not standalone bullish. | Can be paid by routers/sponsors and disconnected from traders. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `successful_transaction_fee_sol` | Fee total from successful transactions. | `sum(total_fee_sol where success=True)`. | Solana transaction status. | SOL | Window | Transaction status timestamp. | Missing upstream stays missing. | Distinguish useful activity from failed spam. | Success does not prove organic trade. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `failed_transaction_fee_sol` | Fee total from failed transactions. | `sum(total_fee_sol where success=False)`. | Solana transaction status. | SOL | Window | Transaction status timestamp. | Missing upstream stays missing. | Manipulation/scam warning. | Failed txs may include benign congestion. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `buy_associated_fee_sol` | Fees associated with buy-side observations. | `sum(total_fee_sol where side='buy')`. | Parsed trade side. | SOL | Window | Trade parser timestamp. | Missing side excludes observation. | Organic flow support when independent. | Fee payer may not be buyer. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `sell_associated_fee_sol` | Fees associated with sell-side observations. | `sum(total_fee_sol where side='sell')`. | Parsed trade side. | SOL | Window | Trade parser timestamp. | Missing side excludes observation. | Distribution/exhaustion context. | Fee payer may not be seller. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `unique_fee_payers` | Count of distinct fee-payer accounts. | `count(distinct fee_payer)`. | Transaction message fee payer. | count | Window | Transaction timestamp. | Empty set when observations exist without payer. | Authenticity context. | Fee payer is not assumed to be trader. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `unique_trade_authorities` | Count of distinct trade authority accounts. | `count(distinct trade_authority)`. | Instruction/account parser. | count | Window | Parser timestamp. | Empty set when unavailable. | Separates payer from actual authority. | Parser coverage dependent. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `independent_fee_payer_clusters` | Count of fee clusters by funding/sponsor/router/payer fallback. | `count(distinct funding_cluster or sponsor_or_router or fee_payer)`. | Wallet graph and transaction parser. | count | Window | Wallet graph snapshot timestamp. | Empty set when unavailable. | Organic confirmation and concentration warning. | Cluster inference must avoid exchange false positives. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `top_fee_payer_share` | Largest fee payer share. | `max(fee_by_payer) / total_fee_sol`. | Transaction fee payer. | ratio | Window | Transaction timestamp. | `missing` when total fee <= 0. | Concentration warning. | Routers can legitimately pay fees. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `top_fee_cluster_share` | Largest inferred cluster share. | `max(fee_by_cluster) / total_fee_sol`. | Wallet graph/funding clusters. | ratio | Window | Wallet graph timestamp. | `missing` when total fee <= 0. | Concentration/manipulation warning. | Cluster quality limits apply. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `fee_concentration_hhi` | Fee payer concentration. | `sum(fee_payer_share ** 2)`. | Transaction fee payer. | ratio | Window | Transaction timestamp. | `missing` when total fee <= 0. | Manipulation warning. | Needs payer clustering for best use. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `creator_connected_fee_share` | Share of fees from creator-connected observations. | `creator_connected_fee_sol / total_fee_sol`. | Wallet graph plus fee observations. | ratio | Window | Wallet graph timestamp. | `missing` when total fee <= 0. | Creator-funded activity warning. | Inferred relationship confidence required upstream. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `bot_sybil_cluster_fee_share` | Share of fees from bot/sybil-linked observations. | `bot_sybil_fee_sol / total_fee_sol`. | Wallet graph/cluster labels. | ratio | Window | Cluster snapshot timestamp. | `missing` when total fee <= 0. | Bot fee spam warning. | Cluster inference can be wrong. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `dust_trade_fee_share` | Share of fees from dust trade observations. | `dust_trade_fee_sol / total_fee_sol`. | Trade parser and notional filter. | ratio | Window | Trade timestamp. | `missing` when total fee <= 0. | Dust manipulation warning. | Dust threshold must be normalized before routing use. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `fee_per_independent_wallet_sol` | Fee spend per inferred independent cluster. | `total_fee_sol / independent_fee_payer_clusters`. | Fee observations and wallet graph. | SOL | Window | Transaction/cluster timestamps. | `missing` when cluster count is zero. | Commitment context. | Not bullish alone. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `fee_relative_to_genuine_buy_notional` | Fee spend relative to non-dust successful buy notional. | `total_fee_sol / genuine_buy_notional_sol`. | Fee observations and trade parser. | ratio | Window | Trade timestamp. | `missing` when genuine buy notional is zero/unavailable. | Authenticity and wash warning context. | Requires reliable notional. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `fee_velocity_sol_per_second` | Fee rate in current window. | `total_fee_sol / window_seconds`. | Fee observations. | SOL/s | Window | Transaction timestamp. | Missing upstream stays missing. | Building/confirmed fee activity. | Must be age/lifecycle normalized before policy use. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `fee_acceleration_sol_per_second` | Change in fee velocity versus previous window. | `current_fee_velocity - previous_fee_velocity`. | Current and prior fee windows. | SOL/s delta | Adjacent windows | Both windows timestamped. | `missing` when no prior window. | Building fee activity. | Needs non-overlapping comparable windows. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `fee_persistence` | Whether fee activity persists across adjacent windows. | `current_total_fee > 0 and previous_window_exists`. | Fee observations. | boolean | Adjacent windows | Both windows timestamped. | False or missing depending upstream state. | Persistence context. | Does not imply organic activity. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `organic_fee_sol` | Fees not linked to creator, bot/sybil, dust, wash, or failed activity. | `sum(total_fee_sol where success and not creator_connected and not bot_or_sybil_cluster and not dust_trade and not suspected_protocol_wash)`. | Fee observations and wallet graph labels. | SOL | Window | Source timestamps required. | Missing upstream stays missing. | Positive confirmation only with independent corroboration. | Organic label depends on upstream classification quality. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |
| `organic_fee_ratio` | Organic fee share. | `organic_fee_sol / total_fee_sol`. | Fee observations and wallet graph labels. | ratio | Window | Source timestamps required. | `missing` when total fee <= 0. | Organic confirmation and manipulation warning. | Not a probability. | Shadow unvalidated. | `signal_engine_v2_fee_commitment@1` |

Classifications emitted by this family are `ORGANIC_FEE_COMMITMENT`, `FEE_ACTIVITY_BUILDING`, `FEE_ACTIVITY_CONFIRMED`, `LOW_FEE_EVIDENCE`, `FEE_PAYER_CONCENTRATION`, `CREATOR_FUNDED_ACTIVITY`, `BOT_FEE_SPAM`, `FAILED_TRANSACTION_SPAM`, `DUST_FEE_MANIPULATION`, and `PROTOCOL_FEE_WASH_TRADING`.

## Legacy Metrics Needing V2 Wrapping

| Metric | Formula summary | V2 status |
| --- | --- | --- |
| `attention_score` | Hand-weighted sum of DEX, wallet, X/social, lifecycle, repeat, and acceleration boosts. | Wrap as heuristic opportunity/attention input, not confidence. |
| `risk_score` | Deterministic composite risk and blocker evidence. | Keep hard-fail signals separate from opportunity scoring. |
| `confidence_score` | Additive event-stage confidence bumps capped by stage. | Rename display use to legacy confidence; V2 confidence must measure evidence quality. |
| `creator_score` | Policy base plus deploy-frequency/cluster/profitability/wallet-age adjustments, clamped 0..1. | Keep direct creator evidence separate from inferred funding-cluster evidence. |
| `elite_score` | Integer point system over early quality signals. | Use as legacy route feature only. |
| `dex_flow_quality_score` | Liquidity, volume, buys, buy/sell, volume/liquidity, and price-change points. | Split into organic flow, paid visibility, and manipulation components. |
| `candidate_ev.net_edge_bps` | `base_upside * (0.70 + attention * 0.60) - slippage - risk_penalty`. | Label as `heuristic_net_edge` until calibrated outcomes exist. |
# Manual Action Engine Metrics

The manual action engine adds shadow decision-support metrics. These are
heuristic and uncalibrated until strict walk-forward validation proves otherwise.

- `probability_target_before_invalidation_pct`: shadow estimate that the target
  is reached before the normal stop or thesis invalidation.
- `probability_catalyst_target_before_invalidation_pct`: same concept for the
  catalyst target.
- `estimated_net_return_pct`: expected net return after execution cost estimate.
- `probability_rug_like_event_pct`: safety estimate for rug-like failure.
- `probability_liquidity_failure_pct`: safety estimate for liquidity failure.
- `probability_sell_route_failure_pct`: safety estimate for sell-route failure.
- `buy_impact_pct`: intended-size executable buy impact.
- `sell_impact_pct`: intended-size executable sell impact.
- `round_trip_cost_pct`: estimated buy plus sell impact, fees, and slippage.
- `maximum_safe_size_usd`: estimated maximum manual size before impact becomes
  unacceptable.
- `data_confidence_pct`: data completeness and freshness estimate.
- `catalyst_confidence_pct`: catalyst verification quality estimate.
- `runner_target_pct`: target percentage of original tokens to preserve.
- `tokens_to_recover_principal`: exact token quantity required to recover
  remaining principal from executable net sell value.
- `drawdown_from_executable_peak_pct`: executable value drawdown from executable
  peak, not chart high.
# Research Corpus Metrics

The offline research corpus records missing states explicitly and separates opportunity, safety, execution, confidence, fee authenticity, wallet independence, holder structure, catalyst, and narrative lineage metrics. Research-only metrics must not be promoted into production policy without walk-forward validation and operator approval.
# X Developer Identity Risk

`x_identity_risk` is a manual risk-control feature generated by the
operator-controlled X identity service.

- `operator_blocked_x_identity`: authoritative token/developer/creator link to
  an active operator-blocked stable X identity.
- `blocked_x_rename_lineage`: verified rename-history link to a blocked alias
  lineage.
- `blocked_x_handle_match_unresolved`: exact current handle or historical alias
  match where the stable X user ID is unresolved.
- `stable_x_identity_unresolved`: stable numeric X account ID has not been
  verified.
- `high_risk_x_dev_identity`: reserved for future severe historical behavior
  once source-backed evidence is available.

Display-name, avatar, biography, and fuzzy-handle similarity are review flags,
not hard-block metrics.
