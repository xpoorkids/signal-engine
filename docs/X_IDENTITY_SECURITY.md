# X Identity Security

Version: `x-identity-risk-v1`

The X identity management API is operator-only. It changes blocklist state,
stable X account IDs, aliases, token links, and operator observations, so it is
disabled by default and protected separately from normal action recommendation
routes.

## Flags

- `SIGNAL_ENGINE_X_IDENTITY_MANAGEMENT_ENABLED=0`: mutation endpoints are
  unavailable unless explicitly enabled.
- `SIGNAL_ENGINE_X_IDENTITY_READ_PUBLIC=0`: sensitive reads require operator
  authentication by default.
- `SIGNAL_ENGINE_OPERATOR_API_TOKEN=`: bearer token used by the operator API.

The token must be long, random, stored only in secret stores, and rotated after
exposure. It must never be committed, logged, stored in SQLite, or passed in URL
query strings.

## Authentication

Protected requests use:

```text
Authorization: Bearer <operator token>
```

The service compares tokens with `hmac.compare_digest`. Missing, malformed, or
incorrect tokens return `operator_auth_required` without exposing whether a
nearby token was close. Disabled management returns
`management_endpoint_disabled`.

## Protected Mutation Routes

- `POST /x-identities/seed`
- `POST /x-identities/blocked`
- `POST /x-identities/{identity_id}/stable-id`
- `POST /x-identities/{identity_id}/current-handle`
- `POST /x-identities/{identity_id}/aliases`
- `POST /x-identities/{identity_id}/disable`
- `POST /x-identities/{identity_id}/restore`
- `POST /x-identities/token-links`
- `POST /x-identities/observations`

## Protected Read Routes

By default these routes also require operator authentication:

- `GET /x-identities/blocked`
- `GET /x-identities/{identity_id}/tokens`
- `GET /x-identities/{identity_id}/history`

Set `SIGNAL_ENGINE_X_IDENTITY_READ_PUBLIC=1` only when exposing operator
blocklist strategy, aliases, stable account IDs, token associations, and notes is
intentional.

## Audit Logging

Mutations write `x_identity_audit_log`. Unauthorized mutation attempts write a
sanitized audit event with the route path and reason. The raw bearer token and
authorization header are never stored. Successful operator mutations store only
a non-reversible actor fingerprint.

## Stable X IDs

Stable X user IDs are public account identifiers. They must be nonempty numeric
strings with a reasonable length. If a stable ID already belongs to another
identity, the operation fails with `stable_x_user_id_conflict`, both identities
remain unchanged, and an audit event is written.
