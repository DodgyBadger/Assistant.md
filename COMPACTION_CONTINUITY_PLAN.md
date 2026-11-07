# Compaction Continuity Plan

Branch: `dev/tool-upgrades`. Status: complete; context guidance updated and focused validation passed. Branch issues: [TOOL_UPGRADES_PLAN.md](TOOL_UPGRADES_PLAN.md). Source: `/home/codeman/docs/AssistantMD/! To Do.md`, reviewed 2026-11-07; conversation decisions take precedence.

## Scope

**Compaction continuity:** Add focused continuing-agent guidance to retrieve an earlier assistant response when the user refers to it and the map lacks enough detail to respond reliably. Leave the compaction author prompt and its salience policy unchanged. This agreed scope replaces the external TODO's proposed author-instruction change.

## Current implementation and affected surfaces

- `core/constants.py` owns `SESSION_MAP_CONTEXT_PREAMBLE` and its version. Add the agreed paragraph and increment `SESSION_MAP_CONTEXT_PROMPT_VERSION` from `session-map-context-v2` to `session-map-context-v3`.
- `core/memory/session_map/checkpoints.py:build_session_map_context_message` embeds guidance and its version into new persisted checkpoint messages. Existing checkpoint content remains stored as authored; this change applies when a new checkpoint context is built, without rewriting runtime history.
- `test_session_map_checkpoint_journey.py` verifies the persisted context-version contract and needs its expected version updated. Existing checkpoint, retrieval, and manual-compaction scenarios exercise the unchanged mechanical path.

## Contract and constraints

- When a user refers to an earlier assistant response (for example, "that plan" or "your recommendation") and the map lacks the needed detail, retrieve the relevant canonical response before proceeding. Do not assume a map preserves complete reasoning or contents.
- Keep retrieval conditional on the user's current need, with no automatic retrieval of the latest response after every compaction.
- Preserve the author prompt, its version, session-wide salience, retention, trigger, evidence-admission, schema, settings, and recovery-card behavior. Do not introduce new map fields or latest-message preservation requirements.

## Acceptance and validation

Review the exact prompt diff directly against the agreed wording and unchanged author prompt. Record a genuine green baseline with `test_session_map_checkpoint_journey.py` and `test_session_map_retrieved_evidence.py`, then rerun those checks with `test_manual_session_map_compaction_journey.py` and `test_session_map_tool_history.py` after the edit. The checkpoint scenario must observe the new persisted prompt version. The remaining checks protect existing compaction, references, retrieval, and ordering; they cannot prove that a live model will follow the new instruction. Do not add exact-prompt wording assertions or a scripted provider response and present it as evidence of improved model judgment. No new mechanical behavior or meaningful red/green defect claim is needed for this scoped prompt edit.

## Execution and evidence

Use real application startup with isolated scenario vaults and system roots. Never use repository `data/` or `system/` as scratch storage. Keep internal services real; use narrow model-provider doubles for deterministic journeys when needed. Record meaningful red/green results, report paths, retained artifacts, and limitations here during implementation. Setup failures do not count as red evidence; record genuine green baselines for working behavior.

Run narrow deterministic scenarios during development. Production Python changes require the complete Production Python Quality Gate. Run `uv run python -m pytest validation/scenarios/integration/core` once the branch is stable during hardening or merge preparation. Experimental and live-service scenarios remain opt-in.

## Open decisions and dependencies

- Author salience constraint (agreed): do not overweight the latest messages, require a recap of the latest assistant response, or expand the map to approximate verbatim retention. Preserve the existing session-level throughline and salience policy. The earlier proposed special preservation rule is not approved.
- Author prompt decision (agreed): leave it unchanged. Existing salience and source-grounding instructions suffice; no author reference clarification is included.
- Retrieval trigger (agreed): continuing-context guidance should encourage retrieving the relevant prior response when the next user turn depends on detail omitted from the map, especially references such as "that plan" or "your recommendation." Avoid an unconditional fetch after every compaction. Existing `SESSION_MAP_CONTEXT_PREAMBLE` already provides general retrieval guidance; the change should address this specific continuity gap.
- Product decisions are settled. Scope is one continuing-context paragraph and its version update, plus the corresponding machine-consumed version expectation. No storage migration or runtime-history rewrite is required.
- Deterministic provider doubles can validate retrieval and provenance mechanics, but cannot prove improved real-model summarization quality.
- Reviewed ADRs 0049 and 0050. Coordinate retrieval guidance with [SESSION_MAP_DISCOVERY_PLAN.md](SESSION_MAP_DISCOVERY_PLAN.md).

## Implementation and evidence

- Added the agreed two-sentence instruction to `SESSION_MAP_CONTEXT_PREAMBLE` and incremented its context version to `session-map-context-v3`. Updated the existing persisted-version assertion and its scenario admission digest. The map-authoring instruction and version are unchanged; no latest-message weighting or verbatim-retention behavior was introduced.
- Genuine baseline: `validation/runs/pytest_20261107_034538_07a13dacc9/report.md`, 2 passing checkpoint/retrieval scenarios before the edit. This is a green baseline, not a demonstrated compaction defect.
- Post-change validation: `validation/runs/pytest_20261107_034639_1b034c18b1/report.md`, 13 passing cases covering checkpoint provenance and stale-commit rejection, verified canonical retrieval, manual compaction, history ordering and pagination, selectors, authorization/forks, and configured limits. The version expectation changes only the deliberately revised machine-consumed prompt metadata; mechanical behavior assertions are preserved.
- Full quality gate passed: `uv run ruff check .`, `uv run black --check .` (779 files), and `uv run mypy api core` (298 source files). Scenario inventory passed. All 34 admission/collection policy checks passed.
- No live-model behavior claim: the change is reviewed prompt guidance. Existing controlled-provider scenarios protect real application mechanics but cannot establish improved model judgment. No new exact-wording tests or fabricated red evidence were added.
- Runtime scope: newly built checkpoint context embeds the new guidance; existing stored checkpoint messages are not rewritten. No schema, settings, retention, author-prompt, recovery-card, or logging contract changed.
- Documentation review complete: `docs/tools/session_ops.md` and ADRs 0049/0050 already describe conditional canonical retrieval, lossy maps, and source verification. This targeted guidance change requires no edits under `docs/`, `README.md`, or `RELEASE_NOTES.md`.
- The full 497-case deterministic profile previously passed for the session tool milestone. It predates this context-guidance edit; rerun the full profile during merge preparation after the remaining branch work. Experimental and live-model scenarios remain opt-in.

## Implementation steps

1. Record the checkpoint/retrieval baseline before editing the prompt.
2. Add the agreed paragraph to continuing-context guidance, increment its version, and update the checkpoint scenario's expected persisted version.
3. Run the focused existing scenarios and retain evidence, keeping unchanged mechanical acceptance conditions intact.
4. Review the prompt diff and run the complete Production Python Quality Gate. A full deterministic profile rerun may occur during merge preparation after the remaining branch work; the previous 497-case run predates this prompt edit.
5. Review relevant documentation (compaction/session-map guidance and ADR 0050 as applicable). If changes are needed, identify affected files, present exact proposed text or diffs in chat, obtain explicit user approval, and then apply approved updates. If current documentation already covers conditional source retrieval, record that no edits are needed.

Next phase: review preparation for this completed milestone; continue other branch issues separately and rerun the full deterministic profile during merge preparation.
