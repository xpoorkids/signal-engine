# Operator X Blocklist

The operator blocklist is seeded from
`config/operator_x_identity_blocklist.yaml` and becomes authoritative in SQLite
after initialization.

Seeded identities:

- `operator_blocked_repeated_coin_rebrands_1`, current handle
  `@Datboicoincto`, with historical aliases `@Trotcatcoin`,
  `@GunnersArtcoin`, `@Meowcoinmovie`, `@Toshbasecoin`, `@shitcoin30_`,
  `@Lennyctocoin`, `@ThomaDeseur`, `@Cheemscoincto`, `@PumpStarss`, and
  `@Rigbzsol`.
- `operator_blocked_repeated_coin_rebrands_2`, current handle `@imthedevvor`,
  with historical aliases `@dogerepublic0`, `@glone_sol`, `@jalen352562`,
  `@glarp_sol`, and `@frank2341235`.

## Tables

- `x_identities`
- `x_identity_aliases`
- `x_identity_blocks`
- `x_identity_token_links`
- `x_identity_observations`

## Operator Controls

- `POST /x-identities/seed`
- `GET /x-identities/blocked`
- `POST /x-identities/blocked`
- `POST /x-identities/{identity_id}/stable-id`
- `POST /x-identities/{identity_id}/current-handle`
- `POST /x-identities/{identity_id}/aliases`
- `POST /x-identities/{identity_id}/disable`
- `POST /x-identities/{identity_id}/restore`
- `POST /x-identities/token-links`
- `GET /x-identities/{identity_id}/tokens`
- `GET /x-identities/{identity_id}/history`
- `POST /x-identities/observations`

The system stores screenshot evidence as manual operator evidence only. Image
recognition alone is not stable account-ID proof.
