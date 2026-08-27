# X Identity Ingestion

Version: `x-identity-risk-v1`

Official token X links are extracted before X identity risk is evaluated. This
keeps the guard effective when a token presents its social profile through DEX
context, metadata, launchpad data, manual review data, or catalyst context.

## Supported Fields

The extractor accepts common structured fields:

- `twitter_url`
- `x_url`
- `twitter`
- `x`
- `twitter_handle`
- `x_handle`
- `links`
- `socials`
- `info.socials`
- nested `market`, `dex_summary`, `metadata`, and `info` objects

DEX Screener social entries with `type=twitter` or `type=x` are treated as
`DexScreener_social`.

## Normalization

The service normalizes:

- `x.com`
- `twitter.com`
- `mobile.twitter.com`
- leading `@`
- trailing slashes
- query strings
- fragments

Display-name, avatar, biography, and fuzzy similarity are not used to create
hard blocks.

## Persistence

Authoritative official links are persisted in `x_identity_token_links` before
action evaluation. Link IDs are deterministic from token, link type, normalized
handle or stable ID, and evidence timestamp, so repeated scans do not duplicate
rows.

## Conflicts

When authoritative sources provide different official X profiles, the system
preserves both observations and adds `X IDENTITY REVIEW REQUIRED` plus
`x_identity_official_source_disagreement`. A confirmed stable blocked ID still
hard-fails even if another source shows a different handle.

## Current Limits

This slice does not scrape websites or resolve X stable IDs through the X API.
Manual stable-ID records and reliable rename-history evidence remain supported.
