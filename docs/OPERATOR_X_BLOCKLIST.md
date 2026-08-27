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
- `x_identity_seed_migrations`
- `x_identity_audit_log`

Seed migrations store the seed name, version, content hash, first application
time, last checked time, application status, and created-row counts. Same-version
seed checks are no-ops except for `last_checked_ts` and an audit event.

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

## Authentication

Mutation routes are disabled unless
`SIGNAL_ENGINE_X_IDENTITY_MANAGEMENT_ENABLED=1` and the request includes
`Authorization: Bearer <SIGNAL_ENGINE_OPERATOR_API_TOKEN>`.

Sensitive reads are also authenticated by default. Set
`SIGNAL_ENGINE_X_IDENTITY_READ_PUBLIC=1` only when exposing operator notes,
aliases, stable X IDs, token links, and historical observations is intentional.

The operator token is never accepted in a query string, never stored in the
database, and never written to audit payloads. Audit rows store only a
non-reversible actor fingerprint.

## Disabled Blocks

Disabling a block is authoritative. Normal startup, `init_schema()`, ordinary
recommendations, and same-version seed application cannot reactivate it. Restore
requires the explicit authenticated restore route or authenticated seed
`force_restore=true`.
