# Catalyst Engine

Catalysts are explicit state attached to a token or manual position. An LLM may
summarize a catalyst later, but it must not be the only proof that the catalyst is
real.

Supported states:

- `RUMOR`
- `UNVERIFIED`
- `VERIFIED`
- `ACTIVE`
- `FLOW_CONFIRMED`
- `HIGH_CONVICTION`
- `PRICED_IN`
- `WEAKENING`
- `INVALIDATED`
- `EXPIRED`
- `FALSE_OR_RETRACTED`

Stored catalyst fields include source, secondary confirmations, first observed
time, expected start/end, market reaction price, price change, buyer/holder/net
SOL flow changes, liquidity change, creator or insider sell activity, catalyst
confidence, flow confirmation, and invalidation reason.

## Catalyst Entry Policy

Catalyst target concept: `+50% before -20% within 30 to 60 minutes`.

`CATALYST BUY NOW SHADOW` requires no hard fail, verified or active catalyst,
on-chain flow confirmation, buy and sell routes, data confidence at least 70%,
catalyst confidence at least 75%, target-before-invalidation at least 52%,
estimated net edge at least +10%, failure risk no higher than 12%, round-trip
cost no higher than 6%, increasing buyer breadth, increasing net SOL flow,
increasing holders, stable or increasing liquidity, and no material creator or
insider selling.

`CATALYST BUY SMALL SHADOW` requires verified catalyst, developing flow
confirmation, target-before-invalidation at least 45%, estimated net edge at
least +4%, failure risk no higher than 15%, data confidence at least 60%, and a
healthy sell route. Suggested manual size is about 25% to 50% of normal intended
size.

`DO NOT CHASE` with catalyst priced-in warning is used when price has already
risen substantially, unique-buyer velocity falls, liquidity growth is small,
holder conversion is weak, fee activity is concentrated, expected net return
collapses, or sell impact rises materially.

Hard safety overrides every catalyst state.

## Catalyst Exit Policy

`CATALYST WEAKENING` warns when the catalyst state weakens but does not yet
require full exit.

`CATALYST INVALIDATED` appears when the catalyst is invalidated or retracted.

`SELL NOW` can appear when invalidation combines with collapsing flow or low
continuation probability.

`EMERGENCY EXIT` appears when safety fails regardless of catalyst state.

