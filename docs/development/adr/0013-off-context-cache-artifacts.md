# 0013 - Use Cache For Off Context Artifacts

## Status

Accepted, backfilled.

## Context

Tools, authoring scripts, and chat-side exploration can produce large temporary
artifacts that are useful but should not be injected wholesale into model
context. Earlier buffer-style mechanisms overlapped with context caching and
made large-result handling harder to reason about.

## Decision

Use the authoring cache subsystem as the shared off-context artifact store for
large or temporary generated artifacts. Chat-side oversized textual tool output
is intercepted by LLM capabilities and stored as cache refs. Authored scripts
can explicitly read and write cache artifacts through helper functions.

## Rationale

Cache refs keep prompt context small while preserving access to the underlying
artifact. A single cache abstraction avoids inventing separate temporary stores
for chat, authoring, and tool overflow. Explicit cache reads also encourage
deterministic local inspection for large text rather than repeated model
summarization of oversized payloads.

## Consequences

- Cache is for generated or extracted off-context artifacts, not for duplicating
  ordinary vault files.
- Vault-backed large reads should usually return refs, metadata, and previews
  instead of copying file content into cache.
- Monty scripts decide explicitly when to persist tool results into cache.
- Chat overflow handling belongs in chat capabilities, not generic tool
  binding.
- `code_execution` and `read_cache(...)` provide deterministic inspection paths
  for cached artifacts.

## Evidence

- Current system map: `docs/development/architecture.md`; tool contract:
  `docs/tools/code_execution.md`
- Recovered sources: PR #40 `cache_subsystem_implementation_plan.md`,
  `MONTY_TOOL_RESULT_CONTRACT_PLAN.md`, `chat_session_sqlite_store_plan.md`
