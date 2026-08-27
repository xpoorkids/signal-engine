# X Identity Risk

Version: `x-identity-risk-v1`

The X identity risk layer tracks public project identities only. It does not try
to identify a real-world person, scrape private information, or prove fraud. It
exists so the operator can apply explicit risk preferences to project-linked X
account lineages.

## Identity Priority

Identity matching uses this priority:

1. Stable numeric X user/account ID.
2. Verified rename-history relationship.
3. Exact current profile URL.
4. Exact normalized current-handle match.
5. Historical alias-only match.

Display names, avatars, bios, and fuzzy username similarity can only create
`X IDENTITY REVIEW REQUIRED`. They do not create deterministic hard failures.

## Enforcement

Confirmed stable-ID matches on authoritative token links return:

`HARD FAIL` with reason `OPERATOR-BLOCKED X DEV IDENTITY`

Verified rename-history matches return:

`HARD FAIL` with reason `BLOCKED DEV IDENTITY LINEAGE`

Exact handle or alias matches without stable-ID verification return:

`AVOID` with reason `POSSIBLE BLOCKED DEV IDENTITY`

The unresolved state blocks positive buy and add recommendations until manual
operator review clears the identity.

When an open manual position is linked to a confirmed stable blocked identity or
verified blocked rename lineage, the action engine returns `SELL NOW` with a
100% recommended sale. This remains manual decision support; no transaction is
constructed or submitted.

Disabled operator blocks are ignored by enforcement. They may remain visible as
historical context, but they do not produce `HARD FAIL`, `AVOID`, or positive
recommendation suppression.

## Link Types

Authoritative links include official token socials, developer profiles, creator
profiles, CTO profiles, metadata socials, DEX Screener socials, launchpad
socials, website socials, Telegram shared profiles, profile-promoted tokens, and
operator-supplied links.

`repost_only` and `mention_only` are exposure links. They create
`BLOCKED IDENTITY PROMOTION EXPOSURE` for review but do not automatically
hard-fail the token.

Official X links are ingested automatically from DEX/metadata/action/review
context before evaluation. Supported URL forms include `x.com`, `twitter.com`,
and `mobile.twitter.com`; query strings and fragments are stripped.

## Seed and Management Safety

The seed blocklist is versioned in `x_identity_seed_migrations`. Normal startup
and ordinary recommendations use a non-destructive seed check. A manually
disabled block cannot be reactivated by restart, schema initialization, or
same-version seed reapplication. Explicit force restore is available only
through the authenticated operator route.

Management routes are disabled by default and require
`SIGNAL_ENGINE_X_IDENTITY_MANAGEMENT_ENABLED=1` plus
`Authorization: Bearer <SIGNAL_ENGINE_OPERATOR_API_TOKEN>`. Sensitive reads are
operator-only unless `SIGNAL_ENGINE_X_IDENTITY_READ_PUBLIC=1`.

## Current Limitations

Stable X ID resolution is manual until an approved X API credential and resolver
are added. No bearer tokens are stored in the database, reports, logs, or raw
artifacts.
