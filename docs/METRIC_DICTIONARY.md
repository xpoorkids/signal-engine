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
