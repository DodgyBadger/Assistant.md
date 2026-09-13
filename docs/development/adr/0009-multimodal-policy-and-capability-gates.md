# 0009 - Gate Multimodal Image Handling With Policy And Capability Checks

## Status

Accepted, backfilled.

## Context

AssistantMD needed image support without weakening markdown-first workflows or
turning every file read into an implicit multimodal payload. Image attachment is
provider-sensitive, can be expensive, and has different safety constraints than
plain text.

## Decision

Handle images through explicit policy and capability gates. Markdown inputs are
parsed into ordered text and image references. Local images may attach only when
policy, model vision capability, text-token limits, and image preflight limits
all pass. Remote image URLs remain reference-only by default. Fallbacks preserve
followable markers instead of silently dropping image context.

## Rationale

Separating text token protection from image attachment policy keeps behavior
predictable. The system can preserve markdown ergonomics while avoiding
surprise downloads, surprise base64 expansion, and provider requests that the
selected model cannot handle. Standard markers make fallback behavior visible to
users and agents.

## Consequences

- `file_read(read)` remains the primary read path for markdown and image
  files.
- `images=auto|ignore` stays the narrow policy surface for authoring inputs.
- Remote refs need explicit future design before download or attachment.
- Multimodal tool returns are not treated as plain text for auto-buffering.
- Validation should cover ordering, missing markers, non-vision fallback, and
  ingestion artifact layout.

## Evidence

- Current system map: `docs/development/architecture.md`
- Recovered sources: PR #38 `image-support-spec.md`,
  `image-support-roadmap.md`, PR #40 `HISTORY_PROCESSOR_BRIEFING.md`
