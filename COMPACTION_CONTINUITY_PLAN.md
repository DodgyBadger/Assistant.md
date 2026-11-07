# Compaction Continuity Plan

Branch: `dev/tool-upgrades`. Status: planned; implementation has not started. Branch issues: [TOOL_UPGRADES_PLAN.md](TOOL_UPGRADES_PLAN.md). Source: `/home/codeman/docs/AssistantMD/! To Do.md`, reviewed 2026-11-07; conversation decisions take precedence.

## Scope

**Compaction continuity:** Improve map-author instructions to preserve the significance and source references of consequential assistant responses, including research conclusions and proposed plans. Encourage the continuing chat agent to retrieve the relevant prior response when the map lacks enough detail. Keep the existing retention policy; do not promote an assistant proposal into a user commitment.

## Current implementation and affected surfaces

- `core/constants.py` contains map-authoring and continuing-context instructions. Inspect the authoring evidence and context rendering paths before changing guidance.
- Inspect `core/memory/session_map/authoring.py`, evidence admission, and continuing-context rendering.

## Contract and constraints

- Preserve the significance and canonical references of consequential assistant responses, without promoting proposals into user commitments.
- Encourage retrieval of the relevant original response when map detail is insufficient.
- Preserve existing retention, trigger, evidence-admission, and map schema contracts.

## Acceptance and validation

**Compaction continuity:** Extend a deterministic session-map compaction journey with a consequential assistant proposal followed by compaction and continuation. Assert valid canonical references, correct proposal status, and successful retrieval of the original response. Review prompt changes directly; deterministic model doubles cannot prove improved real-model summarization quality. Record a green baseline for retrieval behavior that already works.

## Execution and evidence

Use real application startup with isolated scenario vaults and system roots. Never use repository `data/` or `system/` as scratch storage. Keep internal services real; use narrow model-provider doubles for deterministic journeys when needed. Record meaningful red/green results, report paths, retained artifacts, and limitations here during implementation. Setup failures do not count as red evidence; record genuine green baselines for working behavior.

Run narrow deterministic scenarios during development. Production Python changes require the complete Production Python Quality Gate. Run `uv run python -m pytest validation/scenarios/integration/core` once the branch is stable during hardening or merge preparation. Experimental and live-service scenarios remain opt-in.

## Open decisions and dependencies

- Determine the smallest authoring/context instruction changes and required prompt-version updates. No settings or schema migration is currently planned.
- Deterministic provider doubles can validate retrieval and provenance mechanics, but cannot prove improved real-model summarization quality.
- Reviewed ADRs 0049 and 0050. Coordinate retrieval guidance with [SESSION_MAP_DISCOVERY_PLAN.md](SESSION_MAP_DISCOVERY_PLAN.md).

## Implementation steps

1. Inspect authoring and context instructions and choose a consequential-response continuation journey.
2. Record the existing retrieval baseline and add meaningful acceptance conditions for changed mechanical behavior. Review prompt-only changes directly.
3. Implement focused instructions and any required prompt-version updates.
4. Rerun the journey and verify proposal status and canonical references; record evidence and model-quality limitations.
5. Run relevant hardening and quality checks.
6. Review relevant documentation (compaction/session-map guidance and ADR 0050 as applicable), identify affected files, present exact proposed text or diffs in chat, obtain explicit user approval, and then apply approved updates. Documentation edits remain pending until approval.

Next phase: Feature Development, beginning with the remaining contract decisions and acceptance scenarios.
