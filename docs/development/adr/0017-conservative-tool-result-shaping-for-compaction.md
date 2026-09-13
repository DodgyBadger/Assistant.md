# 0017 - Use Conservative Tool Result Shaping For Compaction

## Status

Accepted.

## Context

Long-running AssistantMD chat sessions can produce tool-heavy history before a
compaction checkpoint is written. The compaction system now creates recovery-card
summaries and preserves recent tool exchanges atomically, but older tool-return
content is still serialized into the compaction prompt as source material.

Large tool results are not inherently unimportant. A long directory listing,
search result, file read, or extraction can contain the single fact that explains
a later decision. Many model providers do not expose thinking traces, and a
multi-tool run may not include assistant commentary between tool calls. That
means AssistantMD cannot reliably infer which successful tool result content is
safe to discard.

At the same time, some tool returns are clearly low value as compaction source
material: empty returns and explicit failed or denied returns. Keeping their raw
content can increase compaction cost without improving recovery.

## Decision

Use conservative deterministic shaping for tool-return content before chat
compaction prompts are built:

- Preserve successful non-empty tool-return content in full.
- Do not truncate retained successful tool results.
- Replace structurally empty tool-return content with an omission marker.
- Replace explicit failed or denied tool-return content with an omission marker
  that preserves the tool name and outcome.
- Apply this only to older history that is being summarized; recent preserved
  history remains unchanged.

Do not introduce semantic tool-span compression in this phase. Do not drop
successful tool results based on size alone.

## Rationale

This keeps the first implementation safe. The system can identify empty,
failed, and denied tool returns from structured message fields without guessing
at model intent. It cannot safely determine whether a bulky successful result is
irrelevant unless a stronger signal exists, such as an explicit checkpoint,
artifact reference, or later model-authored summary.

The decision favors correctness and recoverability over aggressive token
reduction. It also leaves room for future shaping policies that use stronger
signals, such as cache refs, mutation records, goal checkpoints, file hashes, or
tool-specific structured metadata.

## Consequences

- Compaction prompts become slightly cleaner for obvious low-value tool returns.
- Successful tool results remain available to the compaction model exactly as
  before, avoiding hidden loss from size-based truncation.
- Token savings are intentionally modest.
- Future tool-heavy span compression remains possible, but it should require a
  richer policy and explicit validation.
- Empty/failed/denied tool returns are still represented in the prompt through
  audit-friendly omission markers.

## Evidence

- Current implementation: `core/chat/compaction.py`
- Validation: `validation/scenarios/integration/core/chat_history_compaction.py`
- Related decisions: ADR 0012 Chat History Broker, ADR 0013 Use Cache For Off
  Context Artifacts
