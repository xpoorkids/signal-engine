# Manual Positions

Manual positions track operator-entered buys and sells without wallet signing.
The engine can know whether the operator owns a token while remaining manual-only.

Tables:

- `manual_positions`
- `manual_position_fills`
- `manual_catalysts`

Routes:

- `POST /positions/manual/buy`
- `POST /positions/{position_id}/buy`
- `POST /positions/{position_id}/sell`
- `POST /positions/{position_id}/close`
- `POST /positions/{position_id}/reopen`
- `PATCH /positions/{position_id}`
- `GET /positions/{position_id}/history`
- `GET /positions/{position_id}/recommendation`
- `POST /positions/{position_id}/recommendation`
- `POST /actions/recommendation`
- `POST /catalysts`
- `PATCH /catalysts/{catalyst_id}`
- `POST /catalysts/{catalyst_id}/invalid`
- `POST /positions/{position_id}/catalyst/{catalyst_id}`

## Position Accounting

Total basis:

`gross buy cost + buy fees + network fees + other entry costs`

The current implementation stores this as `total_cash_invested_usd` and adds buy
fees to basis. Sells reduce unrecovered principal only after net proceeds are
recorded.

Net realized proceeds:

`gross sell proceeds - sell fees - network fees - other exit costs`

Remaining unrecovered principal:

`max(0, total_basis - net_realized_proceeds)`

Average entry price:

`total_basis / original_token_quantity`

Current executable return:

`(realized_proceeds + current_executable_position_value - total_basis) / total_basis`

Executable peak uses executable net sell value, not raw chart high. Drawdown:

`(current_executable_value - peak_executable_value) / peak_executable_value`

## Fill Records

Manual fills store side, token quantity, USD/SOL amount, net cost or proceeds,
execution price, fees, slippage, price impact, optional transaction signature,
fill time, source, and notes.

Supported sources are `manual`, `transaction_signature`, and `shadow`. None of
these require wallet connection or signing authority.

## Principal Recovery

Tokens required to recover principal:

`remaining_unrecovered_principal / executable_net_sell_value_per_token`

If no executable sell quote is available or the per-token net sell value is zero,
the engine returns no token quantity instead of using chart price.

Principal is recovered when:

`net_realized_proceeds >= total_basis`

When a principal-recovery sale would reduce tokens below the runner target, the
recommendation includes two choices: recover principal now or preserve the larger
moon bag while accepting unrecovered principal risk.

