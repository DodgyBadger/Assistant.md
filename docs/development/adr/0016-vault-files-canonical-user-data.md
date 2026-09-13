# 0016 - Treat Vault Files As Canonical User Data

## Status

Accepted, backfilled. Complemented by
[0024 - Use One Chat-Native Vault Explorer Surface](0024-chat-native-unified-vault-explorer.md).

## Context

AssistantMD is a markdown-first system built around user vaults. The app also
maintains runtime databases for chat sessions, cache, scheduler jobs, ingestion
jobs, vault state, summaries, goals, and mutation audit records. Those databases
can make the system faster and safer, but they should not obscure where the
user's durable knowledge work lives.

The vault is also the user's working interface. Users organize projects,
sources, drafts, prompts, and outputs in ordinary folders, including workflows
that start outside AssistantMD in tools such as Obsidian or any file manager.

## Decision

Treat vault files as canonical user data. Keep user-facing durable content in
portable, inspectable files under the vault. Use system databases for runtime
state, indexes, caches, derived summaries, task audit, and safety metadata
rather than as the primary home for user-owned vault content.

## Rationale

Portability and transparency are core design values for AssistantMD. Users
should own their data in formats that are easy to inspect, back up, version
control, sync, and manually edit outside the app. Databases are useful support
infrastructure, but the product should not make the user's vault knowledge
opaque or app-locked.

The filesystem is also an intuitive collaboration workflow. Users can structure
work into subfolders, import PDFs into markdown, collect source material beside
drafts, and then use AssistantMD to apply model intelligence to that existing
workspace. The context a user builds for themselves should be the same context
the agent can work from.

## Consequences

- Markdown and vault-relative files remain the preferred durable artifact shape.
- Database-backed state should either be operational state or derived from
  canonical sources.
- If a database and vault file disagree about user content, the vault file is
  the stronger source.
- Features that generate durable user-facing knowledge should write vault files,
  not only hidden database records.
- Indexes, summaries, manifests, and caches should be rebuildable when
  practical, or clearly documented when they are retained runtime/audit state.

## Evidence

- Current system map: `docs/development/architecture.md`; product overview:
  `README.md`
- Recovered sources: PR #42 `vault_state.md`, PR #20 `importer-plan.md`,
  PR #43 `memory-implementation-plan.md`
