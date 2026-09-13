# `session_ops`

## Purpose

Search prior chat sessions and create or update lightweight session summaries.

This tool is available to chat and authored scripts, including workflows and context scripts, as the direct operation surface for prior-session lookup, transcript search, and session-summary updates.

The selected runtime vault is always the scope. Do not pass or infer a vault parameter.

## Parameters

- `operation`: required. Supported values are `list_sessions`, `summarize_session`, `upsert_session_summary`, `get_session_summary`, and `search_sessions`.
- `session_id`: optional explicit session id. Defaults to the active session when available.
- `mode`: optional search mode for `search_sessions`. Supported values are `search` and `deep`. Defaults to `search`.
- `query`: search phrase for the default `search` mode and for `deep` mode.
- `limit`: optional positive integer result limit. Defaults to 50 for `list_sessions` and 5 for `search_sessions`. `list_sessions` rejects limits above 100.
- `cursor`: optional pagination cursor for `list_sessions`; use the `next_cursor` returned by a prior `list_sessions` call.
- `summary_status`: optional `list_sessions` filter. Defaults to `summarized`, which includes only sessions with stored summaries. Supported values are `summarized`, `any`, `current`, `pending`, and `stale`.
- `filter`: optional metadata filter object for `list_sessions` and `search_sessions`. Currently supports `workspace` only.
- `data`: optional object for `upsert_session_summary`. Supported keys are `summary`, `domain`, `work_product`, `user_intent`, `named_entities`, `source_summary`, `artifacts`, and `metadata`.
- `summarization_model`: optional model alias for `summarize_session`.

## Session Summary Field Contract

Use these fields as short summaries of one chat session. Prefer durable descriptions of the user's work over momentary prompt phrasing.

- `summary`: compact plain-language summary of the session's durable outcome. Capture what happened, the main result or decision, and any important unresolved follow-up. Include only enough detail for a human or future assistant to decide whether the session is relevant; do not preserve a full process log. Target 500-800 characters and never exceed 1,000 characters.
- `domain`: semicolon-separated subject-area tags for the work. Use one to three compact noun phrases, such as `NAWCA grant writing; conservation proposal planning`, `data visualization`, `ServiceNow administration`, or `research synthesis`.
- `work_product`: the concrete thing the user wanted produced or answered, such as `Python script`, `recipe`, `article summary`, `stacked bar chart`, `rewrite`, `donor email`, or `factual answer`.
- `user_intent`: the user's underlying goal or intent after clarification, repetition, or topic drift. Write this as a concise intent phrase that keeps the action-purpose relationship. Choose one primary durable goal rather than listing every sub-task. Omit boilerplate such as `the user wanted to`; target 10-22 words and never exceed 140 characters.
- `named_entities`: only named people, organizations, and places. Use a concise comma- or semicolon-separated text list. Leave empty if there are no named people, organizations, or places.
- `source_summary`: concise bullets summarizing source material or prior context the session appears to have drawn on, based on tool calls/results and the session summary. A source is material that was read, retrieved, imported, or pasted into the session, such as a vault file, web page, imported document, or user-provided source text. Do not create extra bullets for documents, datasets, tools, or evidence that were only mentioned inside another source. The session summary, user intent, and tool log are extraction evidence, not source labels. Do not judge source quality. `source_summary` is returned as provenance for grounding; it is not used as an indexed retrieval field.

For manual writes, put these fields inside `data`. On an existing record, omitted fields are preserved; pass `null` or an empty string to explicitly clear a field. `data.artifacts` is an optional list of artifact objects with `path`, optional `artifact_role`, and `metadata`. `data.metadata` is optional JSON object metadata for the stored session summary row.

## Output Shape

Returns pretty-printed JSON text. Successful operations include a stable `status` and `operation` value, plus operation-specific data such as `session_summary` or ranked `matches`.

Operational failures return a structured failed tool envelope to chat agents. The same failure raises `RuntimeError` when `session_ops` is called directly from Monty, so a required summarization failure cannot be mistaken for a successful script result.

## Filtering

Use `filter` only for deterministic metadata constraints. Filtering changes which sessions are eligible before listing or search ranking happens.

Supported filters:

- `workspace`: restrict results by workspace path.
  - `"current"`: use the current chat session's workspace.
  - `"Path/To/Workspace"`: exact workspace match.
  - `"Path/To/Workspace/*"`: prefix match for descendants under that path.

Examples:

```json
{ "filter": { "workspace": "current" } }
```

```json
{ "filter": { "workspace": "Projects/ClientA" } }
```

```json
{ "filter": { "workspace": "Library/Stewardship/*" } }
```

Do not use general glob patterns such as `"*/wetland_grant"`. Do not use `filter` for fuzzy concepts like topic, intent, domain, named entities, or content. Put those concepts in `query` for `search_sessions`.

## Notes

- `list_sessions` returns a compact page of summarized chat-session rows ordered by latest activity. It is for browsing and overview work, not semantic retrieval. Rows include `session_id`, title, timestamps, message count, history revision, summary status, `domain`, and `user_intent`. It also returns `total_count`, `returned_count`, and `next_cursor` so callers do not mistake a page for the whole available summary set. Use `summary_status: "pending"` only when explicitly looking for sessions that still need summarization.
- `get_session_summary` is read-only. It fetches the stored summary for the current or specified session and returns `status: "found"` or `status: "not_found"`.
- `summarize_session` reads the transcript and structured tool events for the current or specified session, runs Assistant.md's standard extraction policy, upserts the resulting summary fields, attaches any vault files mutated by that chat session as artifacts, and indexes vector-searchable fields.
- `upsert_session_summary` manually stores the `data` values supplied by the caller for the current or specified session. It does not read the transcript or infer missing fields. When updating an existing summary, omitted fields are preserved; pass `null` or an empty string to explicitly clear a field.
- `search_sessions` finds candidate prior sessions.
  - `mode: "search"` is the default. It searches the supplied `query` across session-summary fields using lexical FTS/BM25 evidence plus semantic vector evidence.
  - `mode: "deep"` searches session-summary fields plus raw chat transcripts.
- Without `filter`, `search_sessions` searches broadly across the vault. If the current chat session has a workspace, exact same-workspace matches may be boosted, but other workspaces remain eligible.
- With `filter.workspace`, `search_sessions` searches only matching workspace sessions, then ranks by relevance. Use workspace filtering when the workspace is a hard boundary; rely on workspace boost when the workspace is only a preference.
- Use `search` for normal live-chat lookup when the current session does not yet have a stored summary, or when the user names a specific word, phrase, topic, or concept.
- Use `deep` when the user asks for a broader or transcript-level search.
- For `search` and `deep`, include `query` as a plain natural-language phrase. Do not use explicit boolean syntax such as uppercase `AND`/`OR`. Use a positive integer `limit`.

## Common Calls

List recent sessions:

```json
{ "operation": "list_sessions", "limit": 50 }
```

List sessions with missing summaries:

```json
{ "operation": "list_sessions", "summary_status": "pending", "limit": 50 }
```

List recent sessions in the current workspace:

```json
{
  "operation": "list_sessions",
  "filter": { "workspace": "current" },
  "limit": 50
}
```

Fetch the stored summary for the current session:

```json
{ "operation": "get_session_summary" }
```

Summarize the current session:

```json
{ "operation": "summarize_session" }
```

Manually store summary fields for the current session:

```json
{
  "operation": "upsert_session_summary",
  "data": {
    "summary": "Drafted a donor update about wetland restoration.",
    "domain": "conservation fundraising",
    "work_product": "donor update",
    "user_intent": "prepare donor-facing restoration progress update",
    "named_entities": "North Star Foundation",
    "source_summary": "Drew on wetland restoration progress notes and prior donor reporting context.",
    "artifacts": [
      { "path": "Reports/Wetlands/donor-update.md", "artifact_role": "output" }
    ],
    "metadata": { "source": "manual" }
  }
}
```

Search session-summary fields for a user-named concept:

```json
{
  "operation": "search_sessions",
  "query": "greenhouse gas accounting",
  "limit": 5
}
```

Search only sessions in the current workspace:

```json
{
  "operation": "search_sessions",
  "query": "greenhouse gas accounting",
  "filter": { "workspace": "current" },
  "limit": 5
}
```

Search session-summary fields and raw transcripts:

```json
{
  "operation": "search_sessions",
  "mode": "deep",
  "query": "greenhouse gas accounting",
  "limit": 5
}
```
