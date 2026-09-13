# 0029 - Keep Durable Content Import Narrow And Composable

## Status

Accepted.

Complements
[0007 - Use Settings Backed Model And Tool Binding](0007-settings-backed-model-and-tool-binding.md),
[0011 - Split Ingestion Source Loading From Extraction Strategy](0011-ingestion-source-strategy-split.md),
and
[0027 - Expose Stable Web Capabilities With Explicit Strategies](0027-stable-web-capabilities-and-explicit-strategies.md).

## Context

Chat agents and deterministic Monty workflows need to turn public URLs and
existing vault files into durable Markdown artifacts. The same underlying
conversion is also used by manual URL import and the vault inbox.

Research workflows may additionally discover sources, maintain candidate lists,
track progress, retry failures, deduplicate references, summarize documents, and
organize a library. Embedding those policies in ingestion would create a second
research orchestration subsystem beside chat instructions, skills, goals, and
Monty workflows.

## Decision

Expose one settings-backed `content_import` capability with two operations:

- `submit` creates one durable ingestion job per accepted URL or vault file and
  processes it immediately by default, with explicit queue-only submission for
  large batches;
- `status` reads durable job state by job ID within the active vault.

Use the same ingestion service, job store, strategy registry, rendering, storage,
execution-task attribution, and vault-mutation path for chat, Monty, API, and UI
entry points. Per-job destinations and validated extraction options are part of
the import contract. Durable ingestion jobs remain the authority for immediate
and background execution, status, outputs, and failures.

Keep source discovery, research manifests, source-level deduplication, retry and
resumption policy, index generation, summarization, and library organization
outside the ingestion subsystem. Users compose those behaviors through direct
agent instructions, skills, playbooks, vault Markdown, `goal_ops`, or Monty
workflows.

## Consequences

- Routine tool submission awaits terminal jobs so imported Markdown is
  available in the current agent turn. Queue-only submission returns promptly
  with job IDs for later status inspection.
- The tool and Monty binding expose the same operation and result schema.
- Manual import remains an adapter over shared ingestion behavior rather than a
  parallel conversion pipeline.
- Ingestion does not require a research manifest, batch database, or dedicated
  research scheduler.
- Retry, deduplication, and progress tracking can evolve independently without
  changing the import primitive.
- New import sources and extraction strategies extend ingestion without adding
  provider- or format-specific agent tools.

## Evidence

- `core/ingestion/import_service.py`
- `core/tools/content_import.py`
- `core/ingestion/service.py`
- `api/services/ingestion.py`
- `docs/tools/content_import.md`
- Current system map: `docs/development/architecture.md`
- `validation/scenarios/integration/core/content_import_tool.py`
- Implementation plan: `CONTENT_IMPORT_IMMEDIATE_EXECUTION_PLAN.md`
