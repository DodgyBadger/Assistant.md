# 0011 - Split Ingestion Source Loading From Extraction Strategy

## Status

Accepted, backfilled.

## Context

AssistantMD imports content from files and URLs into vault artifacts. Different
sources need different loading mechanics, and the same source type may support
multiple extraction approaches, such as direct PDF text extraction, OCR, HTML
markdown conversion, or deterministic page-image rendering.

## Decision

Split ingestion into source importers and extraction strategies. Source
importers load raw input into a `RawDocument`. Extraction strategies convert a
raw document into usable text or assets. The ingestion service chooses the
source importer, then runs configured strategies or mode-specific branches until
it has an output to persist.

## Rationale

Loading a source and extracting meaning from it are separate extension points.
Keeping them separate lets AssistantMD add URL, PDF, image, and future source
handlers without hardcoding one pipeline per feature. It also lets extraction
policy be ordered and configured, so deterministic modes, OCR fallback, and
secret-gated strategies can coexist.

## Consequences

- Importers answer how to load a source; strategies answer how to extract from
  it.
- Strategy order and OCR settings belong in configuration.
- `pdf_mode=page_images` can bypass text extraction while preserving the same
  job model.
- Ingestion writes flow through execution-task and vault-mutation tracking when
  run by API or scheduler paths.
- Output layout and provenance are ingestion contracts, not strategy-specific
  accidents.

## Evidence

- Current system map: `docs/development/architecture.md`
- Recovered sources: PR #20 `importer-plan.md`, PR #38
  `image-support-spec.md`, PR #42 `ingestion_vault_activity_plan.md`
