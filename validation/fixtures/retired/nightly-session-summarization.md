---
run_type: workflow
schedule: "cron: 0 2 * * *"
enabled: false
description: Summarize chat sessions with missing or stale stored summaries. Requires the embeddings model alias; see docs/use/getting-the-most.md#search-past-sessions.
---

## Nightly session summarization

This workflow is disabled by default. It requires the `embeddings` model alias
to be configured before summaries can be extracted and indexed. See
`docs/use/getting-the-most.md#search-past-sessions`.

Run it manually while tuning the batch size, then enable it when the
summarization behavior looks right.

```python
"""Summarize a bounded batch of chat sessions with missing or stale summaries."""

# Editable settings. Start with a small batch while testing so a misconfigured
# summarization model, embedding setup, or prompt policy fails quickly and visibly.
BATCH_SIZE = 5
SUMMARIZATION_MODEL = "gpt-mini"


# The helper returns only sessions whose stored summaries are missing or stale.
# That keeps nightly runs bounded and avoids re-summarizing unchanged sessions.
pending = await retrieve_sessions(selection="pending_or_stale_summary", limit=BATCH_SIZE)
sessions = list(pending.items)

if not sessions:
    # `skipped` is a successful workflow outcome: there was simply no work to do.
    await finish(status="skipped", reason="no sessions pending or stale summarization")

summarized = []

for item in sessions:
    # Session metadata is included so the final workflow result is useful in the
    # activity log without requiring the user to inspect each session manually.
    metadata = item.metadata or {}
    session_id = metadata.get("session_id", "")
    title = metadata.get("title", "")
    message_count = metadata.get("message_count", 0)
    history_revision = metadata.get("history_revision", 0)
    summary_status = metadata.get("summary_status", "")

    if not session_id:
        raise ValueError("Cannot summarize a session item without session_id")

    # `summarize_session` reads the transcript, extracts durable summary fields,
    # stores the summary, and refreshes the vector index for session search. It
    # is mandatory: a structured tool failure raises and fails this workflow.
    # Keep this awaited call on one source line until Monty accepts multiline
    # exception frames whose closing line has a smaller column than the opening line.
    result = await session_ops(operation="summarize_session", session_id=session_id, summarization_model=SUMMARIZATION_MODEL)
    summarized.append(
        {
            "session_id": session_id,
            "title": title,
            "message_count": message_count,
            "history_revision": history_revision,
            "summary_status": summary_status,
            "result": result.return_value,
        }
    )

{
    # The last expression is the workflow result returned to the activity log.
    "status": "completed",
    "selected": len(sessions),
    "summarized": len(summarized),
    "batch_size": BATCH_SIZE,
    "summarization_model": SUMMARIZATION_MODEL,
}
```
