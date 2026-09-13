# `goal_ops`

Track durable goals and compact recovery checkpoints for longer work.

`goal_ops` records state only. It does not execute work, write files, schedule workflows, or automatically retry tool calls. Use workflows for repeated or procedural automation; use normal vault markdown files for reports, drafts, evidence, and detailed work logs.

Use `goal_ops` when work is too large, durable, or interruptible to track reliably in the current chat alone. Do not create goals for ordinary questions, quick edits, simple lookups, single-turn answers, or tasks that can be completed immediately.

The main purpose is durable recovery for long-running work. For broad tasks, unless local instructions explicitly require approval gates, keep working through routine batches without stopping for user authorization between each batch. Checkpoint after meaningful batches so the next turn can resume after compaction, request limits, timeouts, network failures, or user interruption. Ask the user only when direction, scope, or an irreversible action actually needs their decision.

Metadata fields are freeform compact JSON objects owned by the caller. Use them for small, stable hints such as workflow names, local tags, ids, or source-path references. Do not embed large notes, reports, extracted evidence, or drafts in metadata; write those to normal vault markdown files and reference the path.

Goal source provenance is system-owned and may appear in results for audit and lookup. Do not provide source fields when creating or updating goals.

Common operations:

- `create_goal`: create a goal.
- `list_goals`: list goals in the active vault.
- `get_goal`: fetch a goal with its latest checkpoint.
- `update_goal`: update title, objective, status, workspace hint, success criteria, plan, or metadata.
- `checkpoint`: record a compact recovery checkpoint.
- `list_activity`: list existing vault mutation activity associated with a goal, including mutated file paths.

Parameters:

- `operation`: required operation name.
- `goal_id`: required for goal-specific operations.
- `status`: optional `list_goals` status filter. Use `"any"` for no status filter.
- `query`: optional `list_goals` title/objective search.
- `limit`: optional result limit.
- `workspace_path_hint`: optional non-authoritative workspace hint filter.
- `data`: operation-specific object payload.

For `list_goals`, `data.source` can narrow results to goals created from a chat session without exposing internal provenance fields:

- `"current_session"`: goals created from the active chat session.
- `"session"`: goals created from `data.session_id`.

`plan` is an optional JSON snapshot on the goal. Keep it lightweight: a short markdown string, a list of task objects, or another compact shape that helps the agent resume work. Replace it through `update_goal`; do not use it for large notes or work products.

Create a goal:

```json
{
  "operation": "create_goal",
  "data": {
    "title": "Prepare Acme renewal briefing",
    "objective": "Create a renewal briefing from project notes and recent meetings.",
    "workspace_path_hint": "Clients/Acme",
    "success_criteria": [
      "Draft briefing exists",
      "Open questions are listed",
      "Source notes are cited"
    ],
    "plan": [
      { "text": "Review source notes", "status": "pending" },
      { "text": "Draft briefing", "status": "pending" },
      { "text": "List open questions", "status": "pending" }
    ]
  }
}
```

Update progress:

```json
{
  "operation": "update_goal",
  "goal_id": "goal_abc",
  "data": {
    "plan": [
      { "text": "Review source notes", "status": "completed" },
      { "text": "Draft briefing", "status": "in_progress" },
      { "text": "List open questions", "status": "pending" }
    ],
    "metadata": {
      "draft_path": "Clients/Acme/renewal-briefing.md"
    }
  }
}
```

Record a checkpoint:

Use checkpoints as recovery cards: summarize what changed, where durable work was written, what remains, and the next concrete actions. Keep detailed evidence or drafts in normal markdown files and reference paths here.

```json
{
  "operation": "checkpoint",
  "goal_id": "goal_abc",
  "data": {
    "summary": "Reviewed May and June notes. Main renewal risks are pricing and security review ownership.",
    "current_state": "Draft briefing is started.",
    "next_actions": ["Pull source quotes", "Finish risk table"],
    "open_questions": [
      "Should pricing recommendation be conservative or aggressive?"
    ],
    "risks": ["Security owner is unclear"]
  }
}
```

List active goals for a workspace:

```json
{
  "operation": "list_goals",
  "status": "active",
  "workspace_path_hint": "Clients/Acme"
}
```

Search goals by title or objective:

```json
{
  "operation": "list_goals",
  "query": "renewal briefing",
  "limit": 5
}
```

List goals created in the current chat session:

```json
{
  "operation": "list_goals",
  "status": "any",
  "data": {
    "source": "current_session"
  }
}
```

Goal activity is derived from the existing vault mutation recorder when work runs with goal context. Use `list_activity` to inspect task-level mutation groups and file paths. `goal_ops` does not attach files or create artifacts.
