# Security Rotation

The repository previously tracked a root `.env` file. Treat every external credential that ever appeared in that file as exposed.

## Required Operator Action

Rotate all external credentials that may have been present in the tracked `.env`, including provider API keys, RPC or WebSocket credentials, Discord webhook URLs, X bearer tokens, tracker credentials, Render deploy hooks, database credentials, and any private wallet or authorization material if it was ever stored there.

Do not commit replacement credentials. Store production values only in Render environment variables or another operator-controlled secret store.

## Git Tracking Repair

The local `.env` file is preserved for the operator, but it must not remain tracked by Git. The repository ignore rules now block `.env` and `.env.*` while allowing `.env.example`.

CI runs Gitleaks on pushes and pull requests so newly committed secrets fail the workflow.

## History Removal

Removing `.env` from the current tree does not remove older copies from Git history. To purge history, use a coordinated maintenance window and `git filter-repo`, then force-push only after every collaborator is ready to reclone or repair local branches.

Example outline:

```bash
git filter-repo --path .env --invert-paths
git push --force-with-lease origin main
```

Do not rewrite shared history automatically from automation or an agent session.

## Visibility

If the source code, policies, or operational workflows are intended to remain proprietary, change the GitHub repository visibility to private. This document does not change repository visibility.

## Logging

Structured logs redact API keys, authorization headers, bearer tokens, webhook URLs, secrets, passwords, seed phrases, private keys, and fields explicitly named as private operator wallet values. Public token mint fields remain visible because they are part of signal evidence.
