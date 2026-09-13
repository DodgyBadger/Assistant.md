# 0008 - Use Documentation First Tool Disclosure

## Status

Accepted, backfilled.

## Context

AssistantMD exposes many tools and authoring helpers. Putting complete tool
contracts into every prompt wastes context, while multiple special discovery
channels make tool behavior harder to predict.

## Decision

Keep always-present tool descriptions concise. Put rich tool and authoring
documentation in markdown under the virtual docs mount, readable through
`file_read`. Prefer targeted documentation reads over large no-argument tool
responses or special metadata endpoints as the normal LLM-facing discovery path.

## Rationale

AssistantMD is markdown-first, so docs-as-readable-artifacts fits the product
model. Thin prompt disclosure keeps initial context small, and virtual docs let
the model inspect only the relevant tool contract or authoring helper when it
needs detail. This also makes tool preference guidance reviewable in normal docs.

## Consequences

- Tool descriptions should be enough to choose a tool, not full manuals.
- Deep usage guidance belongs in `docs/tools/`, `docs/use/`, and the virtual
  docs mount.
- Tool no-argument behavior should stay brief when it exists.
- Authoring and code-execution helper contracts should be discoverable through
  docs rather than large default payloads.

## Evidence

- Current system map: `docs/development/architecture.md`,
  `docs/tools/code_execution.md`, `docs/tools/delegate.md`
- Recovered sources: PR #40 `tool_documentation_disclosure_spec.md`,
  `workflow_python_sdk_parity_checklist.md`
