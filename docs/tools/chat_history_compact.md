# `chat_history_compact`

Check or compact the current chat session history.

Use `operation="status"` to inspect the current message and token estimate, the session's effective compaction strategy, whether it has reached the automatic high watermark, and whether enough older history exists for a manual compaction. Use `operation="compact"` only after the user has explicitly approved compaction.

Parameters:

- `operation`: `status` or `compact`
- `focus`: optional user guidance for what the compaction author should emphasize

The compact operation uses the strategy pinned to the session by its latest context checkpoint, or the configured default when the session has no checkpoint. Recovery-card sessions produce a replacement summary plus the configured newest raw turns. Session-map sessions author a new source-linked map revision and evict complete older conversational groups toward the low watermark. V2 does not use the retained-turn setting; an incomplete active turn remains verbatim.

A session-map compaction can leave only the map in active history. Original messages remain retrievable with `session_ops`.

Manual compaction can run below the automatic high watermark. The `suggested` policy reports when compaction is recommended but waits for an explicit request. The `none` policy disables automatic compaction and proactive suggestions but does not block an explicitly approved manual request.

If a session-map request has no safely evictable complete conversational group, it returns `status="unavailable"` and does not author or commit a new revision.

For session maps, `focus` is a salience lens for the current rewrite, not evidence or permission to change classifications. The author must preserve provenance and unrelated active commitments supported by the transcript.
