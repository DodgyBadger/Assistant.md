# Binary File Copy and Exchange Access Plan

Branch: `dev/tool-upgrades`. Status: planned; implementation has not started. Branch issues: [TOOL_UPGRADES_PLAN.md](TOOL_UPGRADES_PLAN.md). Source: `/home/codeman/docs/AssistantMD/! To Do.md`, reviewed 2026-11-07; conversation decisions take precedence.

## Scope

**Binary files and exchange access:** Support copying PDFs without decoding or rewriting their bytes, and define consistent permitted paths for file tools, including the intended `/exchange` surface. Inspect existing mount and path policies before selecting an implementation; a shared policy is a candidate, not a requirement to introduce a new global allowlist.

## Current implementation and affected surfaces

- `core/tools/file_read.py`, `core/tools/file_write.py`, and `core/vault_state/` own tool adapters, path resolution, file operations, and recorded mutations. Binary mutation support exists; `file_write` currently exposes no copy operation.
- Inspect existing path and virtual-mount policies, binary mutation support, and runtime exchange ownership. Read relevant mutation decisions, including ADR 0025.

## Contract and constraints

- PDF copies preserve every source byte and leave the source intact.
- Apply consistent permitted-path rules for supported exchange access, traversal rejection, and unauthorized mounts. A shared allowlist is a candidate, not a predetermined design.
- Preserve overwrite policy, recorded mutations, and applicable standard inline-edit review.
- Settle PDF reading semantics separately from copying.

## Acceptance and validation

**Binary copy and paths:** Extend `test_file_tools.py` through bound production tools. Copy a PDF and compare complete source/destination bytes; assert overwrite policy, mutation records, and source preservation. Exercise the supported exchange path and rejected traversal, unauthorized mounts, and unsupported text edits. Expected red: absent copy operation or rejected intended exchange access. Settle PDF reading semantics separately from byte copying before adding acceptance expectations.

## Execution and evidence

Use real application startup with isolated scenario vaults and system roots. Never use repository `data/` or `system/` as scratch storage. Keep internal services real; use narrow model-provider doubles for deterministic journeys when needed. Record meaningful red/green results, report paths, retained artifacts, and limitations here during implementation. Setup failures do not count as red evidence; record genuine green baselines for working behavior.

Run narrow deterministic scenarios during development. Production Python changes require the complete Production Python Quality Gate. Run `uv run python -m pytest validation/scenarios/integration/core` once the branch is stable during hardening or merge preparation. Experimental and live-service scenarios remain opt-in.

## Open decisions and dependencies

- Define `/exchange` ownership, addressing, and supported operations from existing runtime and mount behavior; do not grant arbitrary filesystem access.
- Decide whether PDF reading means native model attachment, extracted text, or an explicit unsupported result.
- Identify settings, review-payload, or persisted-state changes before implementation; protect runtime state.

## Implementation steps

1. Inspect binary mutation and mount/path policies and settle supported copy and read operations.
2. Add focused production-tool scenarios and confirm meaningful red results for absent copying or rejected intended exchange access.
3. Implement byte-preserving copy and agreed permitted paths through existing mutation services.
4. Rerun acceptance scenarios to green; verify complete bytes, source preservation, overwrite behavior, mutation records, review where applicable, and rejected paths.
5. Run relevant hardening and quality checks; rebuild CSS only if styling changes require it.
6. Review relevant documentation (file-tool, exchange/mount, and mutation documentation, including ADR 0025 as applicable), identify affected files, present exact proposed text or diffs in chat, obtain explicit user approval, and then apply approved updates. Documentation edits remain pending until approval.

Next phase: Feature Development, beginning with the remaining contract decisions and acceptance scenarios.
