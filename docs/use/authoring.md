# Authoring: Workflow Scripts and Context Assembly Scripts

Assistant.md supports **workflow scripts** for repeatable processing and **context assembly scripts** for controlling the information supplied to chat. Describe your goal to the agent; it can draft and help test the files. This reference covers their format, placement, and execution behavior. Use [Getting the Most](getting-the-most.md) to choose the appropriate kind of customization.

Scripts combine tools for vault files and external information, Python for data manipulation, and `delegate` for model judgment. Pass delegated steps explicit instructions and tool access. If a result is unexpected, inspect `result.metadata["audit"]` to distinguish tool failures from problems with the model's answer.

Enabled built-in tools are available to scripts. Connection-backed tools such as [`gmail`](../tools/gmail.md) also require a ready, sufficiently scoped connection. Network and stdio MCP tools and the general [`shell`](../tools/shell.md) tool are available only to primary interactive chat; workflows, context scripts, and delegate children cannot bind them.

## File Shape

Every authoring file has YAML frontmatter followed by exactly one fenced Python block:

````markdown
---
run_type: workflow | context
description: My automation
---

```python
# your code here
```
````

Scripts execute in the Pydantic Monty sandbox. The [runtime reference](../tools/code_execution.md) documents supported Python features, tool calls, and helper functions. Keep user-editable paths, prompts, model aliases, and thinking settings near the top, and explain the script with concise comments.

Model arguments accept canonical aliases and alternate aliases configured under System → Models.

### Script locations

| Location | Use |
|---|---|
| `AssistantMD/Authoring/` in a vault | Managed workflows and context scripts. One level of subfolders is supported; subfolders starting with `_` are ignored. |
| `system/Authoring/` | Global scripts. Vault scripts take precedence. |
| Elsewhere inside a vault | Project-local `.md` workflows with explicit `run_type: workflow`, run by vault-relative path. These are not automatically discovered, scheduled, enabled, disabled, or listed as managed workflows. |

Startup creates missing system seed scripts. **System → Misc** can refresh existing system scripts from packaged defaults; preserve any custom changes before refreshing.

## Workflow scripts

Use a workflow for scheduled output, batch file processing, or a defined sequence of tool and model calls. Project-local workflows use the same sandbox, timeouts, cancellation, rollback, and run history as managed workflows. See [`workflow_run`](../tools/workflow_run.md) for running them by path.

```yaml
---
run_type: workflow
schedule: "cron: 0 9 * * mon" # omit for manual-only runs
enabled: false
description: Weekly planning
---
```

- `schedule` accepts `cron: MINUTE HOUR DOM MONTH DOW` or `once: YYYY-MM-DD HH:MM`.
- Use weekday names such as `mon` rather than numbers: the scheduler numbers Monday as `0`, unlike standard cron conventions.
- `enabled: false` pauses scheduled runs; manual runs remain available.
- Rescan the vault after changing `enabled` or `schedule`.
- Review manual and scheduled runs on the Dashboard. Long-running tasks can be checked or cancelled there; overlapping runs are queued.

For incremental processing, select candidate files with `file_read`, then pass them to `pending_files`. Use its built-in diff from the previous completed run, and mark successfully processed items complete to establish the next baseline. Consult the [runtime reference](../tools/code_execution.md) for arguments and result shapes.

### Workflow Outcomes and Failures

Reaching the end of a script completes it successfully. The final expression is output data: returning `{"status": "failed"}` does not change the workflow's status.

- Leave required tool calls uncaught. Structured tool statuses of `error` or `failed` raise `RuntimeError` and fail the workflow.
- Catch `RuntimeError` only around a specific call with an expected fallback. A broad catch can hide failure.
- Outcomes such as `not_found` and `already_exists` are ordinary results; inspect `result.metadata["status"]` when branching on them.
- Use `finish(status="skipped", reason="...")` when there is no work, or `finish(status="failed", reason="...")` for an intentional domain failure.
- Explicit `finish(status="completed", reason="...")` is optional when a successful reason is useful.

For example, missing optional input can skip the run:

```python
notes = await file_read(operation="read", path="optional-notes.md")
if notes.metadata.get("status") == "not_found":
    await finish(status="skipped", reason="optional-notes.md does not exist")
```

A fallback should surround only the call that may fail:

```python
try:
    source = await web_extract(urls=PRIMARY_URL)
except RuntimeError:
    source = await browser(url=PRIMARY_URL)
```

A batch that catches per-item failures becomes responsible for the aggregate outcome. Retain concise failure details and call `finish(status="failed", reason=...)` after the loop if any required item failed.

## Context Assembly Script

A context script controls which history, files, and guidance are supplied to chat. Use one when the default loading policy cannot provide the selection or working context you need.

```yaml
---
run_type: context
description: Regular chat with full history
---
```

Use `retrieve_history()` for completed prior history, read-only `latest_message` to inspect the active message, and `assemble_context()` to return the assembled context. History items are safe units: one user message, one assistant message, or a matched tool-call/return pair. Do not append `latest_message` manually; the runtime adds it exactly once.

For text-only standing context, `file_read(operation="read", path="README.md", start_line=1)` reads the full file without attaching embedded images. An ordinary full-file read can return multimodal content instead of a string.

The default script loads available soul, playbook, workspace orientation, skill catalog, and user-note files. Soul, vault playbook, workspace README, and workspace playbook are each bounded to 6,000 characters; the Save User Note skill controls its user-notes limit. See [project context](getting-the-most.md#make-chat-project-aware) and [skill discovery](getting-the-most.md#teach-repeatable-procedures-with-skills) for file conventions.

Select a context script in chat, or set its default under **System → Application Settings**.
