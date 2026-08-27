# Aggressive Catalyst Runner Policy

Policy version: `aggressive-catalyst-runner-v1`.

This is a high-risk manual decision-support profile. Aggressive does not mean
reckless. Hard safety always overrides opportunity, catalyst conviction, and
runner preferences.

## Profit Ladder

Normal non-catalyst position:

- Around +25% executable return: optional 10% to 15% trim.
- Around +50% executable return: optional 15% to 20% trim.
- Around +100% executable return: recover remaining principal using exact
  executable net proceeds.
- After principal recovery: preserve 20% to 30% of original tokens as runner.

Verified catalyst position:

- Around +25%: hold or optional 5% small profit if catalyst remains valid.
- Around +50%: modest 10% to 15% trim when useful.
- Around +100%: recover remaining principal with exact executable quote math.
- Around +200% or higher: optional 10% to 15% trim while preserving catalyst
  runner.

The engine never recommends a full profit exit solely because a target was
reached while safety remains acceptable and the runner floor has not been
preserved.

## Runner Targets

- No active catalyst: 15% to 25% of original tokens.
- Verified catalyst with mixed confirmation: 25% to 35%.
- Verified and flow-confirmed catalyst: 35% to 50%.
- High-conviction catalyst with organic acceleration: 50% to 60%.

The implementation uses the upper aggressive floor of each range for the
default catalyst-runner profile.

Runner target can shrink when catalyst is invalidated, liquidity collapses, sell
route deteriorates, creator or insider distribution begins, concentration becomes
dangerous, flow collapses, manipulation becomes likely, or continuation
probability falls below the floor.

## Trailing Exit

Executable trailing distance uses executable net sell value:

- Normal runner: about 18% to 25% below executable peak.
- Verified catalyst runner: about 25% to 35% below executable peak.

A trailing decline alone does not force `SELL NOW` while catalyst, flow,
liquidity, and sell route remain healthy. It must combine with deteriorating
evidence unless a deterministic safety failure appears.

## Safety Overrides

Full exit can be recommended for no sell route, severe exit impact, major
liquidity removal, creator dumping, connected insider dumping, dangerous token
authority, severe Token-2022 risk, severe wallet concentration, confirmed wash
manipulation, stale/invalid execution data, impossible price impact, or hard
contract-safety failure.

