---
name: Authoring
description: Guidelines for helping users build AssistantMD behavior, skills, workflows, and context customization. Use when the user wants to change how AssistantMD works, support a workflow goal, or create, edit, or debug files in AssistantMD/Authoring/ or AssistantMD/Skills/.
---

Use this skill when helping the user build or change AssistantMD behavior:

- global playbook or workspace playbook guidance
- workspace README orientation
- skill files
- workflow scripts
- context assembly scripts

First, understand the workflow goal. Ask one short, practical question at a time until you know:

- what the user wants to accomplish;
- whether it is global to the vault or local to a project/workspace;
- which vault folders, files, or file types matter;
- whether the task is one-off, repeated on demand, or scheduled/unattended;
- what a good result looks like;
- whether the user wants chat to do the work directly or wants durable support for future sessions.

Prefer the default setup before custom scripts. Recommend the simplest durable customization that fits:

- Use `AssistantMD/playbook.md` for global working policy, source-of-truth rules, review standards, file conventions, or recurring preferences.
- Use `{workspace}/README.md` for project-local orientation: what the folder is, current state, important files, active goals, and constraints.
- Use `{workspace}/playbook.md` for project-local working policy that should be more specific than `AssistantMD/playbook.md`.
- Use a skill in `AssistantMD/Skills/` for a repeatable procedure the chat agent can follow on demand.
- Use a workflow script when the task must run on a schedule, run unattended, or needs deterministic multi-step file processing.
- Use a context assembly script when the default context loading behavior cannot express what should be available at the start of a chat.

Before editing files, state the recommended approach and why it fits. Do not start with workflow or context scripts just because the user says "workflow". First decide whether playbooks, workspace files, or skills are enough.

Before changing authoring files, read the current contract docs:

- `__virtual_docs__/use/getting-the-most.md` for capability selection, skill placement, and built-in discovery behavior.
- `__virtual_docs__/use/authoring.md` for workflow and context script structure.
- `__virtual_docs__/tools/code_execution.md` for Monty runtime features, helper signatures, direct tool calls, and return shapes.

Do not infer the current API from older vault files. Existing user files may predate the current authoring contract.

When writing skills:

- Write plain markdown instructions the chat agent can follow independently.
- Include the goal, when to use the skill, relevant paths, required procedure, expected output, and what to avoid changing.
- Keep the skill focused on one durable procedure or policy area.

When writing authoring scripts:

- Keep user-editable constants such as paths, prompts, model aliases, and thinking settings near the top.
- Use direct tool calls and helper functions exactly as documented.
- For incremental file processing, first use `file_read(...)` to list, search, or otherwise select candidate files, then pass that result to `pending_files(operation="get", items=...)`. `pending_files` does not accept `path`, `pattern`, `glob`, or `search_term` directly. Inspect each returned item's `metadata["pending_diff"]` before writing custom diff or regex comparison logic. When available, `pending_diff["text"]` is the built-in unified diff since this workflow or chat scope last completed that file.
- When the script could use either deterministic parsing or `delegate(...)`, present both options to the user and ask for their preference. Parsing is cheaper and repeatable; delegation is often better for ambiguous extraction, summarization, classification, or judgment.
- Use `delegate(...)` only when the script needs model judgment, and pass explicit instructions and tool access. `delegate(...)` blocks the parent chat turn or workflow step until the child run finishes, so use it for shorter focused tasks. For long-running, broad, or cancellable work, write or use a workflow and start it asynchronously instead of making one large blocking delegate call.
- Leave required direct tool calls uncaught. A structured tool `error` or `failed` result raises `RuntimeError` in Monty so the workflow reports failure instead of silently continuing.
- Catch `RuntimeError` only around one narrowly scoped call when failure is an expected branch, such as falling back from an optional web source. Do not wrap the whole workflow in a broad exception handler.
- Treat non-error outcomes such as `not_found` and `already_exists` as ordinary `ScriptToolResult` values and branch on `result.metadata["status"]` when needed.
- Let normal completion report `completed`. Use `finish(status="skipped", reason="...")` when there is no work, and `finish(status="failed", reason="...")` for an intentional domain failure that is not already represented by an exception.
- A final expression is output data only. Returning `{"status": "failed"}` does not set workflow status.
- When a batch catches per-item failures so later items can continue, retain concise failure details and explicitly finish as failed after the loop if the overall run should be unsuccessful.
- Make one small change at a time.

Offer to test using the `run_workflow` tool. Note that this can only test workflow scripts, not context assembly scripts. Fix any errors returned by the tool. Even if no errors are returned, inspect the artifacts to ensure they meet requirements.
