# X Identity Seed Migrations

Version: `operator-x-identity-blocklist-v1`

Seed application is versioned, idempotent, and non-destructive. The seed file is
`config/operator_x_identity_blocklist.yaml`; the database is authoritative after
the first application.

## State Table

`x_identity_seed_migrations` stores:

- `seed_name`
- `seed_version`
- `content_hash`
- `first_applied_ts`
- `last_checked_ts`
- `application_status`
- `identities_created`
- `aliases_created`
- `warnings_json`

The primary key is `seed_name + seed_version`.

## Normal Startup

Application startup calls a cheap migration check. If the seed version already
exists, only `last_checked_ts` and the skipped audit event are updated. The
normal startup path never force-restores operator state.

## Non-Destructive Rules

Seed sync may create missing seeded identities, create missing aliases from a
new seed version, fill null-only fields, and update seed provenance.

Seed sync must not reactivate a disabled block, disable an active block, replace
a manually verified stable X ID, overwrite operator notes, remove aliases,
change token links, or overwrite a manually changed current handle.

## Force Restore

`POST /x-identities/seed` accepts `{"force_restore": true}` only through the
authenticated management route. This is explicit operator intent and is the only
seed path allowed to restore seeded blocks.

## Concurrency

Seed application uses a short `BEGIN IMMEDIATE` transaction. Concurrent startup
or route calls serialize on SQLite and converge to one logical migration without
duplicate aliases or block-state resets.
