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

## Link Types

Authoritative links include official token socials, developer profiles, creator
profiles, CTO profiles, metadata socials, DEX Screener socials, launchpad
socials, website socials, Telegram shared profiles, profile-promoted tokens, and
operator-supplied links.

`repost_only` and `mention_only` are exposure links. They create
`BLOCKED IDENTITY PROMOTION EXPOSURE` for review but do not automatically
hard-fail the token.

## Current Limitations

Stable X ID resolution is manual until an approved X API credential and resolver
are added. No bearer tokens are stored in the database, reports, logs, or raw
artifacts.
