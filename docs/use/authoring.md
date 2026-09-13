# Authoring: Workflow Scripts and Context Assembly Scripts

Assistant.md has a unified authoring surface for two types of automation: **workflow scripts** and **context assembly scripts**. Managed and scheduled scripts live in `AssistantMD/Authoring/` inside your vault. A workflow that belongs to one project may instead live with that project and be run explicitly by vault-relative path. Context scripts always remain in the managed Authoring location.

You don't need to write these files by hand. Describe what you want to the chat agent; it can draft, edit, and help you test. Use this document as orientation.

The common script pattern is to use tools and helpers for host-owned access to capabilities such as vault files, message history, and web search; use ordinary Python to manipulate, filter, sort, and transform data; and use `delegate` when the script needs model inference. When deterministic parsing and model judgment are both reasonable, present the tradeoff to the user. Parsing is cheaper and repeatable; delegation is often better for ambiguous extraction, summarization, classification, or judgment. Always pass `delegate` explicit instructions; never assume it knows anything about the environment or available tools.

Authored scripts can bind settings-backed built-in tools that are enabled for the current execution. Connection-backed tools such as [`gmail`](../tools/gmail.md) also require a ready, sufficiently scoped connection. Network and stdio MCP tools and the general [`shell`](../tools/shell.md) tool are currently available only to primary interactive chat; workflows, context scripts, and delegate children cannot bind them.

When a delegated step gives odd results, inspect `result.metadata["audit"]` before changing the script. The audit summarizes the child agent's tool calls, arguments, return previews, and tool errors, helping distinguish tool or file problems from a poor model answer.

For incremental file processing, first use `file_read(...)` to list, search, or otherwise select candidate files, then pass that result to `pending_files(operation="get", items=...)`. `pending_files` does not accept `path`, `pattern`, `glob`, or `search_term` directly. Each pending item can include `item.metadata["pending_diff"]`, the built-in diff from the last time the current workflow or chat scope completed that file. Prefer this metadata to maintaining separate copies or writing custom comparisons. After processing the selected items, call `pending_files(operation="complete", items=selected)` so the next run has a fresh baseline.

---

## File Shape

Every authoring file has YAML frontmatter followed by exactly one fenced Python block.

````markdown
---
run_type: workflow | context
description: My automation
---

```python
# your code here
```
````

Rules:

- Exactly one ` ```python``` ` block. No more, no less.
- Scripts execute in a limited Python sandbox using Pydantic Monty.
- The [runtime reference](../tools/code_execution.md) documents the Monty environment, supported Python features, and shared helper surface used by chat-side `code_execution` and authored scripts.
- Start with the simplest script that can do the job, test it, then add complexity only when the result proves it is needed.
- Include comments that help the user understand the script.
- Define user-editable variables such as paths, titles, prompts, model, and thinking mode at the top of the script.

Files can be organized in subfolders one level deep inside `Authoring/`. Subfolders starting with `_` are ignored.

---

## Workflow Scripts

A workflow script is an automation that runs Python code against your vault. Use one when you want to:

- Generate or transform files on a schedule (daily notes, weekly summaries, reports)
- Process a batch of files (inbox triage, tagging, indexing)
- Chain multiple LLM calls with conditional logic

Reusable, discoverable, or scheduled workflows belong in `AssistantMD/Authoring/`. Project-local workflows can live anywhere inside the vault and run through `workflow_run(operation="run" | "start", workflow_path="...")`. They must use a `.md` extension and explicitly declare `run_type: workflow`. Project-local workflows use the same sandbox, tools, timeouts, cancellation, rollback, and run history, but are not discovered, scheduled, enabled, disabled, or shown as managed workflows.

**Frontmatter for workflow scripts:**

```yaml
---
run_type: workflow
schedule: "cron: 0 9 * * mon" # optional — omit for manual-only
enabled: false # true to activate scheduled runs
description: Weekly planning
week_start_day: monday # optional, default monday
---
```

- `schedule` supports `cron: MINUTE HOUR DOM MONTH DOW` or `once: YYYY-MM-DD HH:MM`
- Use weekday names such as `mon`, `tue`, or `sun` in cron schedules. The current scheduler uses APScheduler 3.x, whose numeric day-of-week values do not match standard cron: `0` is Monday and `1` is Tuesday. Weekday names avoid that ambiguity.
- `enabled: false` pauses scheduled runs; manual runs always work
- Rescan your vault after changing `enabled` or `schedule`
- Manual workflow runs appear as managed tasks in the Dashboard. Long-running runs can be checked or cancelled there, and overlapping runs are queued.

### Workflow Outcomes and Failures

A workflow that reaches the end of its script completes successfully. Its final expression is script output data, but values inside that expression do not control the workflow status. For example, returning `{"status": "failed"}` does not fail the workflow.

Use normal Python failure handling:

- Leave required direct tool calls uncaught. Structured tool statuses of `error` or `failed` raise `RuntimeError` inside Monty and fail the workflow.
- Catch `RuntimeError` only around a specific call when failure is an expected branch. Avoid wrapping the whole workflow in a broad exception handler.
- Non-error outcomes such as `not_found` and `already_exists` remain ordinary tool results. Inspect `result.metadata["status"]` to branch on them.
- Use `finish(status="skipped", reason="...")` when there is no work to do.
- Use `finish(status="failed", reason="...")` for an intentional domain failure that is not already represented by an exception.
- `finish(status="completed", reason="...")` is available when an explicit successful terminal reason is useful; otherwise reaching the end is enough.

A required call normally needs no extra status handling:

```python
summary = await delegate(
    model="gpt-mini",
    prompt="Summarize the selected notes.",
)
```

Expected absence is a normal result rather than an exception:

```python
notes = await file_read(operation="read", path="optional-notes.md")
if notes.metadata.get("status") == "not_found":
    await finish(status="skipped", reason="optional-notes.md does not exist")
```

Catch a failure narrowly when the script has a real fallback:

```python
try:
    source = await web_extract(urls=PRIMARY_URL)
except RuntimeError:
    source = await browser(url=PRIMARY_URL)
```

Batch workflows may continue after individual failures, but catching those errors makes the script responsible for the aggregate outcome:

```python
failures = []

for item in selected_items:
    try:
        await process_item(item)
    except RuntimeError as exc:
        failures.append(f"{item.ref}: {exc}")

if failures:
    await finish(
        status="failed",
        reason=f"{len(failures)} selected items failed; first error: {failures[0]}",
    )
```

---

## Context Assembly Script

A context assembly script shapes what the chat agent knows at the start of a conversation. Use one when you want to:

- Control which history the agent sees (curate, summarize, or filter prior turns)
- Load relevant files or skill listings into the agent's context automatically
- Build a specialized assistant mode for a particular project or workflow script

**Frontmatter for context scripts:**

```yaml
---
run_type: context
description: Regular chat with full history
---
```

Most context scripts use three core pieces: `retrieve_history()` to read completed prior session history, read-only `latest_message` to branch on the active message, and `assemble_context()` to hand assembled context to the chat agent. `retrieve_history().items` counts safe units: one user message, one assistant message, or one matched tool-call/return pair. Do not append `latest_message` manually; the runtime adds it exactly once after the assembled context.

The packaged default context script loads `AssistantMD/soul.md`, `AssistantMD/playbook.md`, lightweight user notes configured by `AssistantMD/Skills/save_user_note.md`, and the skill catalog from `AssistantMD/Skills/` when those files are present. These are vault-owned markdown files; customize or replace the context script when you want a different loading policy. The default script bounds each soul, vault playbook, workspace README, and workspace playbook to 6,000 characters; the Save User Note skill controls the user-notes limit.

Workflows can use `retrieve_sessions(selection="pending_or_stale_summary")` to select current-vault chat sessions that lack a stored summary or whose summary is stale. It returns session metadata only; use `retrieve_history()` or `session_ops` when a workflow needs to process a specific session. Stale selection compares the current persisted history revision with the revision recorded when the summary was extracted.

Select which script to use in the Chat UI. Set a default in **System → Application Settings**.

Scripts are discovered from `AssistantMD/Authoring/` (vault) and `system/Authoring/` (global). Vault scripts take precedence. Startup creates missing system seed scripts, and **System → Misc** can refresh existing system seed scripts from packaged defaults.
