# 0030 - Use Mistral As The Only Built-In OCR Provider

## Status

Accepted.

Complements
[0011 - Split Ingestion Source Loading From Extraction Strategy](0011-ingestion-source-strategy-split.md)
and
[0029 - Keep Durable Content Import Narrow And Composable](0029-narrow-durable-content-import-boundary.md).

## Context

AssistantMD needs high-quality PDF-to-Markdown conversion without adding a
heavy document-processing runtime. Mistral OCR provides native Markdown,
document images, structural blocks, tables, separated headers and footers,
confidence data, direct public-URL input, and useful model and usage provenance.

Pydantic AI models multimodal inputs for model conversations but does not
abstract dedicated OCR endpoints or their structured document-processing
options. Introducing a provider-neutral OCR framework before implementing a
second provider would add speculative models, capability translation, and UI
branches without proving a stable shared contract.

Docling and comparable self-hosted systems also carry runtime and operational
costs that do not belong in the AssistantMD application process.

## Decision

Support Mistral as the only built-in OCR provider. Call its dedicated REST OCR
endpoint through AssistantMD's adapter rather than coupling ingestion to the
Mistral SDK or treating a Pydantic AI chat model as an OCR service.

Keep extraction strategies format-aware. PDF OCR is exposed as `pdf_ocr` and
image OCR as `image_ocr`; future Word, presentation, or other document strategies
retain their own format identity even if they share provider transport or
response parsing internally.

Prefer Mistral OCR before local PDF text extraction in the default strategy
order when configured. Secret-gated strategy resolution skips OCR cleanly when
Mistral is unavailable, leaving `pdf_text` as the credential-free fallback.
Explicit local-only, OCR-only, and page-image choices remain available.

Do not add a provider-neutral OCR abstraction, a second provider, or an in-process
heavy OCR dependency until a concrete use case requires it. A future provider
should be implemented as another format-aware strategy and may justify a small
normalized result boundary after at least two real adapters expose proven shared
behavior. Heavy or self-hosted processors should be integrated through an
external service API.

## Consequences

- Mistral-specific enrichments remain explicit and opt-in at the current adapter
  boundary.
- OCR capability metadata can accurately describe one implemented provider
  without promising unsupported provider portability.
- Provider transport can evolve independently: direct URL, inline bytes, and
  future file upload remain execution details of `pdf_ocr`.
- Local PDF extraction preserves offline, privacy-sensitive, and credential-free
  operation.
- Adding another OCR provider requires an intentional architectural review and
  may extend or supersede this decision.
- Docling, PaddleOCR, olmOCR, and GPU frameworks are not added to the core
  AssistantMD dependency footprint.

## Evidence

- `core/ingestion/strategies/pdf_ocr.py`
- `core/ingestion/strategies/mistral_ocr_common.py`
- `core/ingestion/capabilities.py`
- `core/settings/settings.template.yaml`
- Current system map: `docs/development/architecture.md`
- `docs/tools/content_import.md`
- `validation/scenarios/integration/core/mistral_ocr_contract.py`
- Design plan: `pdf-import-design.md`
