# Vault Link Standardization

## Goal

Use visible full-path wikilinks (`[[Library/! Primary Sources/Lehmann Springs/index.md]]`) for new agent-authored vault references and picker insertion. Support ordinary Markdown links alongside wikilinks in the shared chat/vault renderer, and retain historical plain and inline-code `@path` references. Implementation is authorized; no transcript or vault migration is needed.

## Starting boundaries

Chat and the vault viewer already share `chat-markdown.js` rendering and `file-reference-links.js` enhancement. The enhancer recognizes some existing Markdown anchors, but replaces their authored label with `@path`, guesses path boundaries in prose, and resolves using the selected chat vault and workspace rather than an explicit source-document location. Standard Markdown removes prose-boundary guessing for newly authored links; path resolution still needs a shared, explicit policy.

## Proposed testable slices

1. Wikilinks always contain full vault-relative paths without aliases or basename guessing. Markdown targets in chat resolve from the vault root, and targets in vault documents resolve from the source-file directory; a leading slash explicitly selects the vault root. Decode Markdown URL destinations once, not literal wikilink filenames. Resolve dot segments without allowing traversal above the vault root. Preserve author labels and ordinary web/fragment links.
2. Extend the shared enhancer with explicit vault/source-document context, preserve authored link labels, and route files/directories through the existing viewer/Explorer. Keep external URLs and fragment links out of vault lookup. Missing local targets must not silently navigate to application routes, and decoded paths must remain behind the existing traversal and vault-access checks. Do not duplicate server filesystem policy in JavaScript.
3. Adopt wikilinks in the flight-card reference instruction and reference-picker insertion. Keep historical reference rendering compatible; do not rewrite saved chats or vault documents. Do not enhance links in fenced code blocks.

## Validation and scope

Extend frontend link-controller and Markdown-rendering tests plus `integration/core/vault_file_reference_api`. Cover the reported punctuation-and-space path, authored labels, encoded targets, nested-document relative links, vault-root links, folder targets, external URLs, fragments, missing files, traversal rejection, and legacy `@path` references. Verify both chat and nested vault previews use the same resolver with their own source context. No new settings or persistence migration. Run the production Python quality gate for the flight-card constant change and the deterministic core profile once stable.

## Completion and validation

Implemented in the existing shared modules: a local Marked inline extension renders escaped wikilink spans without interpreting path characters as Markdown; the shared enhancer resolves those spans and ordinary Markdown anchors with explicit source context. Both current and historical vault previews pass their vault and source-file path through the existing renderer callbacks. Link labels remain visible, local targets cannot navigate into application routes during resolution, and file/directory navigation captures the resolved vault. The flight card and picker now emit full-path wikilinks. Existing legacy-reference detection and workspace basename fallback remain available. No backend API/schema changes, persisted-history rewrites, settings changes, or CSS changes were required.

The file-reference and chat-rendering module suite passed 14/14, covering the tokenizer, code-block exclusion, escaping, picker insertion, source-context forwarding, root/document-relative routing, literal versus URL-encoded paths, labels, folder navigation, external URLs, fragments, invalid and missing targets, repeated enhancement, and legacy references. JavaScript syntax checks passed for all four changed scripts. The full deterministic `integration/core` profile passed 134/134 (`20261009_192425_517566`); Ruff, Black, MyPy, and `git diff --check` passed. Conventional browser smoke testing remains unperformed because the installed Chromium cannot launch without missing OS libraries. Automated frontend checks exercise the renderer and controller contracts but do not replace that interactive check.
