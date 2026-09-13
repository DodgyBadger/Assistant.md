# `code_execution`

## Purpose

Run constrained local Python in Assistant.md's Monty runtime.

This document serves two purposes:

- it documents the chat tool `code_execution`, which runs a snippet against the current chat session
- it is also the main helper reference for authored workflows and context assembly scripts, which run in the same Monty environment without the outer tool wrapper

## Tool Argument

- `code`: constrained Python snippet to execute

## Tool Output Shape

Returns the script's final expression, explicit `finish(...)` result, printed output, or a structured execution error as plain text. Inside the script, direct tool calls return objects with `return_value`, `metadata`, `content`, and `items`.

## The Monty Runtime

This tool does not run CPython. It runs **Monty** — a Python interpreter written in Rust with its own bytecode VM.

Monty is actively developed upstream, so Assistant.md documents the subset it intentionally supports for authoring rather than every Monty VM feature. Treat the type checker and compile step as the source of truth for the exact runtime available in the current installation.

Monty runs each Assistant.md script as a fresh script execution. Your final line should be an explicit expression that evaluates to the value you want to return. Do not rely on implicit printing.

### Supported Authoring Pattern

Assistant.md examples and validation scenarios stay within this subset:

- ordinary expressions, assignments, loops, conditionals, comprehensions, and helper function definitions
- async helper calls using `await`
- f-strings and type hints where they help readability
- common imports used by Assistant.md examples, including `json`, `sys`, `typing`, `asyncio`, and `pathlib`
- host-provided dataclasses and helper result objects, such as `RetrievedHistoryResult`, `HistoryMessage`, `ToolExchange`, and `LatestMessage`
- external function calls through Assistant.md helpers and direct tools, such as `file_read(...)`, `file_write(...)`, `delegate(...)`, and `assemble_context(...)`
- pre-execution type checking with Monty's bundled `ty` integration

### Authoring Guardrails

- Do not define custom classes in authored scripts; use dictionaries, lists, helper result objects, and small functions.
- Do not depend on arbitrary standard-library modules; stick to modules used in Assistant.md examples unless you have compiled the script successfully in this environment.
- Do not import third-party packages; use Assistant.md helpers for file access, model calls, history, and tool calls.
- Do not depend on state from previous script executions; pass state through files, caches, or explicit helper results.
- Do not assume every CPython builtin exists. Prefer known helper result attributes directly, such as `result.return_value`, `result.metadata`, and `result.items`. Current Monty supports `getattr(...)` and `hasattr(...)` for helper result objects when dynamic checks are useful.
- For bulk or long-running work, split discovery from mutation. Use one script to gather explicit candidates with read-only operations, then a separate script to mutate a bounded list of exact targets. Do not combine recursive discovery and mutation in one script.
- For bulk vault reorganizations, prefer staged migration. Build or populate the new structure first, then produce a cleanup report for old source paths. Do not delete old source trees after a copy-style reorganization unless the user explicitly asks for destructive cleanup.
- If a Python construct matters to your script and is not shown in Assistant.md examples, compile the script before relying on it.

When you find yourself reaching for something outside this list, simplify the approach rather than adding imports or boilerplate.

### Type checking

The runtime runs `ty` on your code before execution using type stubs for all helpers. If you pass wrong argument types or unknown keyword arguments to a helper, execution will not start and you will get a type error instead of a runtime failure.

## Runtime Surface

The Monty helper surface is shared across chat-side execution, workflows, and context scripts.

Scope comes from the current runtime context:

- in chat, file and history access come from the active session and the tools enabled for that run
- in authored workflows and context scripts, access comes from the current script host and whatever tools/helpers that runtime exposes

Available helpers and reserved inputs:

- direct tool functions such as `file_read(...)`, `file_write(...)`, `delegate(...)`, `web_extract(...)`, or `browser(...)`: invoke a tool by name with keyword arguments
- `pending_files(...)`: filter a file result set to the pending (unprocessed) subset and explicitly complete the items you finished
- `retrieve_sessions(...)`: select current-vault chat session metadata, such as sessions pending summarization
- `retrieve_history(...)`: read broker-owned conversation history as safe atomic units
- `assemble_context(...)`: build structured message history for downstream chat-style generation
- `read_cache(...)`: open one cached oversized tool result by cache ref inside the current runtime context
- `parse_markdown(...)`: turn markdown into frontmatter, sections, headings, code blocks, and image refs
- `finish(...)`: end the script intentionally with a `completed`, `skipped`, or `failed` terminal status
- `date`: resolve common date tokens — `date.today()`, `date.this_week()`, etc.; pass `fmt` for strftime formatting
- `latest_message`: read-only latest message metadata for context script decision making

Use ordinary Python for filtering, sorting, selection, and control flow around those helpers.

## Helper Notes

### `read_cache`

- use this when chat reports that an oversized tool result was stored in a cache ref
- reads cached content for the current runtime context; in chat that means the current chat session
- check `artifact.exists` before accessing `artifact.content`

### `pending_files`

- `operation="get"` filters a `file_read` result down to the pending subset
- `pending_files` does not accept `path`, `pattern`, `glob`, or `search_term`; first call `file_read(...)` to list/search/select candidate files, then pass that result as `items`
- always inspect `item.metadata["pending_diff"]` before computing your own diff; it is the built-in "changed since this workflow/chat scope last completed this file" signal
- `pending_diff["available"]` is true when the current scope has previously completed that file and the retained baseline can be read
- when available, `pending_diff["text"]` contains a unified diff from the last completed baseline to the current file, and `pending_diff["has_changes"]` indicates whether the diff has content
- when unavailable, `pending_diff["reason"]` explains why, such as `processed_baseline_unavailable` on the first run
- `operation="complete"` marks only the items you actually finished processing

### Direct Tool Calls

- call tools by their configured names, for example `await file_read(operation="read", path="notes/example.md")`
- `code_execution` itself is excluded to prevent recursive self-invocation
- direct tool results expose `return_value`, `metadata`, `content`, and `items`
- use `result.return_value` for the canonical tool result; `content` is reserved for Pydantic AI side-loaded content
- every bound direct tool includes `result.metadata["tool_name"]`; prefer branching on `result.metadata["status"]` when the tool reports structured status
- a structured direct-tool status of `error` or `failed` raises `RuntimeError` in Monty; catch it only around a call whose failure is an expected branch
- non-error outcomes such as `not_found` remain `ScriptToolResult` values and can be inspected through metadata
- do not parse the human-readable `return_value` from list/search tools when a structured field exists; use `result.items`, `result.metadata`, or a more specific tool operation
- mutating scripts should receive an explicit bounded target list from the caller; they should not recursively discover their own target set

### `assemble_context`

- for conversation history, fetch explicit messages through `retrieve_history(...)` and pass `history.items`
- `retrieve_history(...)` counts safe history units: user message = 1, assistant message = 1, matched tool call + return = 1 `ToolExchange`
- `retrieve_history(...)` items are structured objects; slice, remove, or reorder those objects when curating context, then pass the remaining objects back to `assemble_context(...)`
- use `item.text` or `history.text` when you intentionally need clean prompt text for `delegate(...)`; this omits provider-native payload fields while preserving the original object for assembly
- in context assembly scripts, use read-only `latest_message` only to decide what prior history, files, or instructions to include
- do not add `latest_message` to `history` or `context_messages`; the chat runtime appends it exactly once after your assembled context
- use `latest_message.exists`, `latest_message.role`, `latest_message.content`, and `latest_message.text` when context selection should depend on the active request

### `retrieve_sessions`

- `retrieve_sessions(selection="pending_or_stale_summary")` returns current-vault chat sessions that do not yet have a stored summary or whose summary is stale
- returned items contain session metadata only, including `session_id`, `title`, `created_at`, `last_activity_at`, `message_count`, `history_revision`, `has_summary`, `summary_status`, `summary_updated_at`, `summary_message_count`, `message_count_delta`, `new_message_count`, `summary_history_revision`, and `history_revision_delta`
- stale summary selection compares the current persisted history revision with the history revision recorded when the summary was extracted
- use this helper to select sessions, then call `session_ops(operation="summarize_session", session_id=...)` when a workflow should summarize selected sessions

### `parse_markdown`

- returns typed objects with attributes — use `heading.text`, not `heading.get("text")`
- prefer this over regex or manual string parsing for any markdown or extracted article text

### `finish`

- only supported status values: `completed`, `skipped`, `failed`
- keyword-only — `await finish(status="skipped", reason="...")`
- the script result should be the last expression or an explicit `await finish(...)`

### `date`

- resolves the same shared date tokens used elsewhere in Assistant.md
- pass `fmt` to control formatting using strftime — e.g. `date.today("%Y-%m-%d")`
- week-based values honour the current workflow or runtime `week_start_day`

## Common Patterns

### Chat tool invocation

Use the outer `code_execution(...)` wrapper only when calling the chat tool directly.

```python
code_execution(
    code="""
{
    "today": date.today("%Y-%m-%d"),
    "tomorrow": date.tomorrow("%Y-%m-%d"),
}
""",
)
```

### Raw Monty script for authored files

Inside a workflow or context script, write only the Python script body. Do not wrap it in `code_execution(...)`.

```python
history_result = await retrieve_history(scope="session", limit="all")

await assemble_context(
    history=history_result.items,
    instructions="Keep the answer concise.",
)
```

### Branch on the active chat message

Use `latest_message` for context selection, not for manual message insertion.

```python
history_result = await retrieve_history(scope="session", limit="all")
context_messages = []

if latest_message.exists and "trigonometry" in latest_message.text.lower():
    guide = await file_read(operation="read", path="Projects/trig/study-guide.md")
    if guide.metadata.get("status") == "completed":
        context_messages.append({"role": "system", "content": guide.return_value})

await assemble_context(
    history=history_result.items,
    context_messages=context_messages,
)
```

### Explore a large web extraction

Extracts a page, parses the structure, pulls a target section, and returns a compact summary. Uses `read_cache` first so re-running the script does not repeat the extraction call.

```python
code_execution(
    code="""
# Read from cache if the extraction was already stored
artifact = await read_cache(ref="tool/web_extract/call_abc123")
if not artifact.exists:
    result = await web_extract(urls=["https://example.com/article"])
    parsed = await parse_markdown(value=result.return_value)
else:
    parsed = await parse_markdown(value=artifact.content)

# Orient: what sections does this page have?
headings = [h.text for h in parsed.headings[:20]]

# Pull the section we care about
target = next(
    (s for s in parsed.sections if "methodology" in s.heading.lower()),
    None,
)

{
    "today": date.today("%Y-%m-%d"),
    "headings": headings,
    "target_section": target.content[:1500] if target else "NOT_FOUND",
}
""",
)
```

### Split Discovery From Mutation

For broad vault changes, use `code_execution` as small deterministic batches. First discover candidates with read-only calls and return an explicit list. Then run a separate mutating script over that exact list. This makes the work reviewable, resumable, and less likely to turn into an unbounded crawler.

For reorganizations, treat cleanup of old source trees as a separate destructive step. After a copy-style migration, report old source folders, file counts, and likely duplicates; do not delete those source trees unless the user explicitly asks for destructive cleanup.

Discovery example:

```python
code_execution(
    code="""
listed = await file_read(operation="list", path="Archive", recursive=True)

candidates = listed.metadata.get("empty_directory_candidates", [])

{
    "candidate_count": len(candidates),
    "candidates": candidates,
}
""",
)
```

Mutation example:

```python
code_execution(
    code="""
targets = [
    "Archive/old-empty-folder",
    "Projects/completed",
]
results = []

for path in targets:
    result = await file_write(
        operation="delete",
        path=path,
        confirm_path=path,
    )
    results.append({
        "path": path,
        "status": result.metadata.get("status"),
        "removed": result.metadata.get("removed_directories", []),
        "skipped_non_empty": result.metadata.get("skipped_non_empty_directories", []),
    })

results
""",
)
```

The mutating script should not rediscover targets recursively. If the candidate list is too large, process a small slice, checkpoint progress, then run another bounded batch.

### Process a batch of pending vault files

Lists a directory, filters to the pending subset, uses built-in pending diff metadata when available, then marks the processed items complete. Batches to a small slice to stay within a single execution.

```python
code_execution(
    code="""
listed = await file_read(operation="list", path="inbox")
pending = await pending_files(
    operation="get",
    items=listed,
)

if not pending.items:
    await finish(status="skipped", reason="no pending files")

results = []
selected = pending.items[:5]
for item in selected:
    diff = item.metadata.get("pending_diff", {})
    if diff.get("available") and diff.get("has_changes"):
        # Prefer this built-in diff over ad hoc regex comparison against saved copies.
        changes_since_last_complete = diff["text"]
    else:
        changes_since_last_complete = ""

    doc = await file_read(operation="read", path=item.ref)
    parsed = await parse_markdown(value=doc.return_value)
    results.append({
        "ref": item.ref,
        "changed_since_last_complete": bool(changes_since_last_complete),
        "diff": changes_since_last_complete,
        "title": parsed.frontmatter.get("title", item.ref),
        "sections": [s.heading for s in parsed.sections],
    })

await pending_files(
    operation="complete",
    items=selected,
)

results
""",
)
```

## Notes

- Helpers are injected for you - you do not need to import anything.
- Standard-library imports such as `import json` are fine when needed.
- file, memory, and web access should generally go through direct tool calls or dedicated helpers
- Always return a value or `await finish(...)` — do not rely on side effects alone
- cached oversized tool results are available through `read_cache(ref=...)`
- If a cached tool result already exists, use `await read_cache(ref="...")` instead of re-running the source tool
- Some pages add wrapper headings such as extraction banners, navigation, or TOC chrome — prefer the article's real prose sections over page furniture
- prefer returning a compact final value instead of printing large text
