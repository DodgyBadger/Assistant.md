# Release Notes

## v0.8.0

v0.8.0 makes Assistant.md extensible beyond its built-in tools. It can connect to remote MCP services, run local stdio MCP providers in an optional advanced Linux environment, and search and read Gmail or create unsent drafts through a first-class connection. This release also adds encrypted credential storage and explicit deployment access controls.

### Connect tools and services with MCP

- Add, authorize, test, enable, and manage MCP connections from **System → Connections**. Compatible Streamable HTTP and SSE servers can expose remote services directly to chat.
- MCP connections support OAuth, bearer tokens, custom headers, or no authentication. OAuth can finish through the browser callback or a manual flow for headless installations.
- Each connection has its own tool allowlist. Assistant.md discovers permitted tools, keeps large tool catalogs out of the prompt until needed, and makes them available through normal chat tool search.
- Multiple connections remain independently named and owned, so the same kind of service can be configured more than once without mixing credentials or tools.
- Public MCP endpoints require HTTPS. A trusted private-network server can opt in to HTTP on that individual connection.
- Connection changes and credential updates recover safely if Assistant.md is interrupted or restarted partway through the operation.

### Run stdio MCP providers and commands in the advanced shell

- The optional advanced execution mode gives chat a persistent, general-purpose Linux home and workspace in a separate, resource-limited container. Chat can install packages, use CLIs and runtimes, and run bounded foreground commands.
- The supplied `advanced` Compose profile starts a version-matched companion and pairs it with Assistant.md automatically. Restricted execution remains the default.
- Local MCP providers that communicate over standard input and output can now be registered alongside network MCP connections. Assistant.md launches them on demand in the advanced shell and applies the same tool discovery, allowlists, budgets, and result handling used for network connections.
- A bundled **Advanced Shell MCP Setup** skill can help install a provider and produce a connection definition for review in **System → Connections**.
- Files in the advanced-shell home and workspace survive ordinary container restarts. Processes and temporary files do not; continuously running services still belong in their own managed container.
- Host files are invisible unless explicitly mounted. Keep mounts narrow and prefer Assistant.md's encrypted connection credentials over credentials stored directly in the shell.

### Search Gmail, save PDF attachments, and create drafts

- Connect one or more Google accounts under **System → Connections**, choose a default account, and let chat search mail or read relevant messages and complete threads.
- Assistant.md can create recipient-free, unsent plain-text drafts when explicitly enabled on a Gmail connection. The user adds recipients, reviews, and sends in Gmail; Assistant.md cannot send, modify, move, or delete mail.
- Search results stay compact until chat requests a specific message or thread. Chat can save a PDF attachment to any vault-relative path for later import, organization, or processing with other available tools. Existing files are preserved automatically with numbered filenames.
- Attachment downloads are disabled by default and can be enabled per Gmail connection with a 25 MB default limit; attachment bytes are written directly to the vault and never placed in chat context.
- Gmail tools appear only when a configured account is ready, and each account's OAuth grant remains separate.
- Draft creation is disabled by default, has a configurable body limit, and requires reauthorizing the opted-in connection. Ambiguous creation failures are never retried automatically, avoiding accidental duplicate drafts.

### Protect connections and application access

- Model-provider keys, connection credentials, and OAuth tokens are now stored in an encrypted database rather than a plaintext YAML file.
- Connection metadata, encrypted credentials, and OAuth state now update atomically in `system/access.db`, preventing a partial credential or connection change from becoming active.
- Assistant.md now requires an explicit ingress-authentication mode. Deployments can use the built-in single-owner token, trust an authenticating reverse proxy, accept only direct loopback peers, or deliberately use unprotected `disabled` mode for recovery and testing.
- **System → Infrastructure** reports the active authentication and execution posture and warns when the application is exposed without authentication.
- Built-in authentication does not provide TLS; remotely reachable deployments still need HTTPS at their ingress.

### Misc

- The product and GitHub repository are now styled **Assistant.md**. Fresh clones use an `Assistant.md` checkout folder, while existing checkouts can retain their current local folder name. Environment variables, image names, container names, and other technical identifiers remain unchanged.
- Reworked the user guides around an outcome-first path: start with Chat and Vault Explorer, then add project context, skills, connections, advanced execution, delegation, session search, and automation as needs grow. Installation and upgrade guidance now follows the renamed repository and favors clear UI-led setup.
- Content imports invoked by agents now run immediately by default and return completed output paths or durable failures in the same tool call. Large multi-file submissions can opt into background processing with `queue_only`.
- Reduced recurring chat prompt overhead by relying on bound tool schemas rather than repeating a capability list. The default context now loads soul and playbook instructions only when those files exist in the vault.
- Consolidated the developer architecture, setup, security, tool, and decision documentation around the current runtime and connection model.
- Hardened connection cleanup, cancellation, startup recovery, network policy, advanced-shell pairing, and release-image consistency.
- Updated the supported Compose layout so future topology changes arrive through the tracked base file while deployment-specific paths, mounts, networks, and build choices live in `.env` or `docker-compose.override.yml`.
- Nightly session summarization now recovers from invalid streamed structured output by retrying through a validation-compatible execution path.
- Recovery cards no longer show reconstructed archived tool-call details. Current tool statuses remain stable while their detail modal is opened and closed.
- Chat now keeps tool arguments and results out of routine browser payloads. The compact tool list shows only safe status metadata and estimated result size; full details are fetched on demand and cleared when the modal closes.

### Migrating to v0.8.0

1. Back up your vaults, `system/`, `.env`, and current Compose files. Keep the `.env` backup separate: restoring encrypted credentials requires both the credential database and its installation key.
2. If upgrading a repository checkout with a locally edited Compose file, save it before pulling, then take the new tracked base file:

   ```bash
   mv docker-compose.yml docker-compose.pre-v0.8.yml
   git pull --ff-only
   ```

   Do not copy the old file back over the new `docker-compose.yml`.

3. Copy `.env.example` to `.env` if needed. Preserve an existing `ASSISTANTMD_SECRETS_KEY`, or generate one by following the [v0.8.0 upgrade guide](docs/setup/upgrading.md#upgrading-to-v080). Move your existing data path, system path, timezone, authentication mode, authentication secret, and public URL into `.env`.
4. Move structural deployment customizations—custom builds or UID/GID values, proxy networks, extra mounts, and similar changes—into `docker-compose.override.yml`, using the supplied example as a starting point.
5. To enable the optional advanced shell, add both settings below. Otherwise, leave them unset and continue using restricted mode.

   ```dotenv
   COMPOSE_PROFILES=advanced
   ASSISTANTMD_EXECUTION_MODE=advanced
   ```

6. Validate and restart the deployment:

   ```bash
   docker compose config --quiet
   docker compose down
   docker compose pull
   docker compose up -d
   ```

   For a checkout that builds images through the override example, use `docker compose build` instead of `docker compose pull`.

7. Open **System → Infrastructure** and confirm that encrypted secrets and the intended authentication and execution modes are ready. If advanced mode is enabled, wait for the advanced shell to report `ready`.
8. If you use the packaged default context script, refresh system scripts under **System → Misc**. Save any custom changes to the existing system script first; refresh installs the new explicit-only soul and playbook behavior.
9. Confirm that model-provider API keys were imported, then reconnect existing OAuth accounts. Configure and test Gmail and MCP connections under **System → Connections**. Attachment downloads and draft creation are disabled on each Gmail connection until you explicitly enable them. Enabling draft creation requires reauthorizing that connection for Gmail compose permission.

On first startup, Assistant.md imports legacy non-OAuth secrets into encrypted storage and preserves the old file at `system/migration_backups/secrets.yaml.bak`, or the next available numbered name when that file already exists. Losing `ASSISTANTMD_SECRETS_KEY` requires re-entering stored credentials. Routine `docker compose down` preserves advanced-shell pairing and persistent files; do not add `-v` unless you intend to delete those Docker volumes.

## v0.7.3

### Long-running chats recover more reliably

- Chats can recover from temporary model-stream interruptions without repeating tool calls that already finished.
- Dropped browser connections reconnect to the running chat. Reloading a session also reattaches to its active task instead of abandoning the work.
- If live updates are no longer available, the UI waits for the background task and reloads the saved conversation rather than presenting an incomplete response.
- Recovery protects side effects: safe unfinished work may retry, interrupted vault changes are rolled back before the chat restarts, and uncertain external actions stop with a clear failure instead of being repeated.

Recovery covers interruptions while Assistant.md is still running. An active task cannot resume after the server or container itself restarts.

### Better visibility into tool calls

- The chat tool list now shows which calls are still running and whether each finished successfully, failed, or was interrupted.
- Tool details include status and elapsed time. Full stored arguments and results can be opened and copied even when the chat preview is shortened; oversized outputs continue to use their saved artifact reference.
- Structured tool failures are shown as failures instead of appearing as successful calls merely because the tool returned a response.

### Safer, more useful delegates

- Delegate agents receive their tool budget and are prompted to stop in time to return a concise handoff.
- Separate limits for tool calls, model requests, running time, and repeated identical failures help contain runaway delegate loops. These limits remain configurable in System settings.
- When a delegate reaches a limit or fails, it now returns available partial progress, usage, completed-call evidence, and artifact references so the parent can continue with a narrower follow-up instead of repeating all the work.

### Runtime compatibility

- Updated Pydantic AI, Pydantic AI Harness, and Pydantic Monty for the recovery and delegate reliability improvements.
- Existing chat history and vault data remain compatible; this release adds no database migration.

### After upgrading

Restart Assistant.md to load the updated runtimes. No data migration is needed.

## v0.7.2

### Huge update to import pipeline

- The chat agent and workflow scripts can now batch import local PDFs or URLs that resolve to HTML or PDF using the new content_import tool.
- Each import can choose its destination directory. When omitted, Assistant.md uses the configured default import destination.
- Upgraded the pdf_ocr import strategy to take advantage of the latest Mistral OCR features (OCR enrichments, structural blocks, tables, separated headers and footers, and page- or word-level confidence).
- Revamped the Import panel in the UI. Now reports all import jobs with live links to source and destination files, option to reload / edit the job and clearer presentation of import options.
- New installations prefer Mistral OCR and fall back to local PDF text extraction. If Mistral is not configured, OCR is skipped cleanly and local extraction continues.
- Added new global import settings.

The import tool deliberately handles conversion rather than research policy. Agents, skills, playbooks, and workflows remain free to decide how sources are discovered, tracked, retried, and organized.

### Workflow scripts can now live anywhere in the vault

- `workflow_run` can now run or start a workflow Markdown file from any vault-relative path, allowing project-specific processing scripts to live beside the library, notes, and outputs they manage.
- Project-local workflows use the same sandbox, tools, timeout, cancellation, rollback, and durable run history as managed workflows.
- Explicit path-based workflows remain separate from the managed catalog: they are not discovered, scheduled, enabled, disabled, or used as context templates. Managed and scheduled workflows continue to live in `AssistantMD/Authoring/`.

### Reliability and development

- `web_extract` now rejects PDFs and other binary responses with guidance to use `content_import`, preventing oversized binary tool results from disrupting a chat stream.
- Browser storage is optional. Assistant.md continues initializing with in-memory defaults when embedded in a restricted or opaque-origin frame where `localStorage` is unavailable.
- `scripts/dev run` now uses the checkout's repository-local `data/` and `system/` directories by default, matching the persistent development state developers expect.

### After upgrading

1. Restart Assistant.md and apply any pending database migrations shown under **System > Misc**.
2. If System Notices offers **Repair settings from template**, run it to add the `content_import` tool and new ingestion settings while retaining custom configuration.
3. Existing values of `ingestion_pdf_default_strategies` are preserved. To use the new OCR-first recommendation, set the order to `pdf_ocr`, then `pdf_text`. Keep `pdf_text` first or select **Local Text Only** when documents must not be sent to Mistral.

## v0.7.1

### Backend authorization foundation

- Chat sessions now have durable internal ownership, and queued or background work retains the authority under which it was created.
- Shared backend authorization boundaries reduce the risk that new API services bypass access checks.
- Ownership-sensitive session operations consistently conceal inaccessible session identifiers, including create-or-touch requests, preventing those identifiers from being used to probe ownership.
- This release has no visible app changes and does not change routes or API request and response payloads.

### Development setup

- Development is supported on both general-purpose hosts and the devcontainer through one `scripts/dev` workflow. Each checkout uses a pinned Python 3.13 UV environment and isolated local runtime state.
- Setup can repair stale virtual environments, prepare frontend dependencies, optionally install Playwright Chromium, run the development server, diagnose prerequisites, and invoke focused validation scenarios.
- Validation runs now finish with a failure-focused digest containing direct evidence links and rerun commands, and retain Markdown and JSON run indexes so failures remain easy to find after terminal output has scrolled away.
- Checkout-local development now exposes built-in tool documentation through the same virtual docs mount used by container deployments.

## 2026-08-02 - v0.7.0

### Vault explorer and inline editing

Assistant.md can now handle routine vault browsing, writing, organization, and recovery without leaving the chat window.

- **Browse and edit:** Open the Vault Explorer from the chat toolbar or workspace selector. Browse the full vault, preview rendered Markdown, edit UTF-8 text, copy or add paths to a prompt, set the session workspace, create files and folders, upload local files, move or rename paths and delete files or empty folder trees.
- **Open files from chat:** File and directory references in assistant messages become live links that open the Vault Explorer at the referenced path.
- **Review agent edits:** Switch a chat session between Normal and Inline edit modes. Inline edit presents each `file_write` operation as an editable approve-or-deny card with the opportunity to make user-edits before approving or provide reasons for denying.
- **Restore and roll back:** Open a file's revision history in the Vault Explorer to preview or restore an earlier version. From Assistant.md Activity, undo all file changes made by a completed chat turn, workflow run, or Explorer action in one step. Rollback itself can also be undone.

### Improved observability

Workflow outcomes now survive restarts and appear on the Dashboard with run history, duration, and failure or skip reasons. An attention summary highlights missed, failed, and timed-out runs.

System Activity now retains searchable daily history for up to 30 days, subject to a size limit, and supports raw JSONL export. High-volume validation and helper events no longer crowd out user-relevant activity.

### Web retrieval

Web research now uses stable `web_search`, `web_extract`, and `web_crawl` capabilities with an explicit strategy for each. Strategy failures are reported clearly and never silently fall back to another provider.

The built-in curl extraction strategy shares secure, bounded retrieval with URL ingestion. Chromium remains available through `browser` for dynamic pages; it requires the 2 GB browser-capable profile and should be disabled on smaller deployments.

### Misc

- Split the 5,081-line API service module into domain modules while preserving the existing API surface.
- Cleared 663 production mypy errors and made Ruff and Black clean.

### Breaking changes and upgrade guidance

Revamped several tools:

- `file_ops_safe` and `file_ops_unsafe` replaced with `file_read` and `file_write`.
- `web_search_duckduckgo`, `web_search_tavily`, `tavily_extract` and `tavily_crawl` have been replaced by `web_search`, `web_extract` and `web_crawl`.
- Choice of provider for each web tool now lives in System settings: `web_search_strategy`, `web_extract_strategy` and `web_crawl_strategy`.

All custom workflows and context assembly scripts that use the old tools must be updated.

Per-chat tool selection has also been removed. Assistant.md's core functionality depends on the full tool suite working together, so maintaining a different tool set for each chat is no longer practical. The built-in tool suite will stay deliberately small enough not to overwhelm the context window. Users who wish to disable a tool can do so globally with the `disabled_tools` setting (which replaces the previous `enabled_tools` setting).

**After upgrading:**

1. Restart Assistant.md to run registered database migrations. If System / Misc still reports pending Database Migrations, run them there.
2. Back up any local changes under `system/Authoring`, then run **Refresh System Authoring Scripts** from System / Misc. This overwrites `system/Authoring`; vault scripts under `AssistantMD/Authoring` are not touched.
3. If System Notices offers **Repair settings from template**, run it. This rewrites retired web tool names in settings, converts the old tool allowlist into `disabled_tools`, adds the strategy settings, and preserves custom tool entries. Review `disabled_tools`, `web_search_strategy`, `web_extract_strategy`, and `web_crawl_strategy`; set Tavily explicitly for each capability that should use it.
4. Update custom workflows and context scripts under each vault's `AssistantMD/Authoring` directory. Chat can inspect and update these automations for you. Replace retired tool calls as follows:
   - `file_ops_safe` or `file_ops_unsafe`: use `file_read` for `read`, `list`, `search`, and `frontmatter`; use `file_write` for `write`, `append`, `edit_line`, `replace_text`, `move`, `delete`, and `mkdir`.
   - `web_search_duckduckgo` or `web_search_tavily` → `web_search`
   - `tavily_extract` → `web_extract`
   - `tavily_crawl` → `web_crawl`

5. Review custom scripts that catch or ignore tool failures. Structured tool `error` and `failed` results now raise `RuntimeError` inside Monty. Catch only expected probe failures; non-error outcomes such as `not_found` remain ordinary tool results.

## 2026-07-06 - v0.6.10

### OpenAI OAuth

Assistant.md now includes an experimental OpenAI OAuth connection path for the built-in OpenAI provider.

- OpenAI can be connected from System settings without storing a Platform API key, using a Codex/ChatGPT-compatible OAuth flow.
- Device-code login is supported, so remote or server-hosted installs can show a short code that you enter in your local browser.
- API-key auth remains the default and fully supported path for OpenAI.
- OAuth can be globally disabled, and API-key fallback is opt-in so Assistant.md does not silently switch billing paths.

### Misc

- Interrupted chat turns can now be retried manually from the stored unfinished turn state.
- OpenAI-backed background model calls now stream through the task-owned chat path more consistently.
- Chat history compaction summaries now stream progress instead of waiting for a single final response.
- Workflow schedule docs now recommend weekday names such as `mon` and `tue` because APScheduler 3.x numeric weekday values differ from standard cron.
- Fixed chat rendering so simple dollar values are not accidentally formatted as LaTeX.

## 2026-06-23 - v0.6.9

- Provider thinking parts are no longer persisted in message history by default, reducing chat token usage and making it more reliable to switch the same session between model providers.
- Improved display of thinking parts in the chat UI (for providers that expose it and if persistence is enabled).
- Reduced noisy vault-refresh entries in the activity log; idle scheduled scans now stay out of the rotating activity log unless they detect changes or failures.
- Fixed a rare vault identity race that could make vault activity and mutation history appear under inconsistent vault IDs.
- Clarified in model settings that only OpenAI is currently supported for embedding.
- Session summarization now fails before extracting summaries when embedding setup is missing or unusable, with clearer logs so users can fix the configuration without ending up with incomplete summaries.
- Installation doc now calls out the `OPENAI_API_KEY` requirement for embeddings.
- Fixed UI rendering bug that falsely transformed dollar amounts into LaTeX.

## 2026-06-19 - v0.6.8

### Further hardening of long-running tasks

Significant refactor to make Assistant.md more reliable during long chat turns and other background work.

- Chat turns now run as managed execution tasks, so refreshing or closing the browser tab does not cancel the model run.
- Task cancellation and rollback are ordered more safely, reducing the chance that cleanup runs while a task is winding down.
- Oversized multipart image uploads are rejected earlier, before creating a chat task.
- The old synchronous chat execution path has been retired; chat now uses one task-owned execution path for streaming, cancellation, transcript persistence, and tool activity.
- The Dashboard shows active execution tasks for chat, workflows, compaction, ingestion, and related work in one place.
- Fixed bug in files_ops_safe.list that caused empty results on root directory when using the recursive flag.

## 2026-06-16 - v0.6.7

- Fix: chat history compaction now uses the retained recent turns as supersession context, so compaction cards are less likely to preserve stale current objectives or next steps.

## 2026-06-16 - v0.6.6

### Long-running sessions

Assistant.md is more resilient when an agent is working through a long-running, complex goal.

- Added lightweight goal tracking tool (`goal_ops`) so agents can record status, success criteria, notes, checkpoints, and activity without relying only on chat memory.
- Chat history compaction now defaults to automatic and creates recovery-oriented checkpoints, so long sessions can keep moving without manually managing the context window.
- Tool-heavy history is preserved more safely across compaction, including multi-tool batches and paired tool call/results.
- If a chat turn fails after the user message has already been accepted, Assistant.md records recovery context for the next turn instead of silently losing the thread.
- Model request limits are configurable, and model, delegate, workflow, API, and web-tool failures now return clearer recovery information.

### UI improvements

The chat UI is easier to use during long, tool-heavy sessions.

- UI improvements for dense chat sessions, such as compact tool-call lists and better sidebar formatting for scripts, arguments, and results.
- Archived tool activity from compacted history remains visible from the compaction card without taking over the main chat view.
- Forking a compacted chat now starts from the visible compacted conversation, not the full pre-compaction archive, so the fork stays practical to continue.

### Misc

- File tools handle attachments and other non-markdown files more consistently.
- Fix: directory deletion can clean up empty directory trees, reports non-empty directories more usefully, and large directory listings are bounded to avoid runaway output.
- Fix: refreshing packaged system workflows now preserves the user's enabled or disabled state.
- Fix: system workflows route through the same governor as other workflow runs.
- Workflow runs now report heartbeat/status information.
- Code execution guidance and Monty-facing docs/type stubs were updated so common script patterns are easier for agents to get right.
- Added a goal cleanup utility under System / Misc.
- Node dependencies received security updates.
- Started an ADR library under docs and backfilled from the repo commit history.

## 2026-06-07 - v0.6.5

- Fork a chat session from any assistant message to branch an existing conversation while preserving the original session.
- Newly summarized sessions without a custom title now use the extracted domain as their chat title, so session lists are easier to scan.
- Session summaries now fail visibly if vector indexing cannot complete, so incomplete summaries do not quietly look healthy while missing searchable context.
- Delegate tool call limits and timeouts are now configurable.
- Updated Pydantic AI and Monty dependencies, including the recent Monty sandbox security fix.
- Improvements to the chat UI.
- Improvements to the build-guide.
- Improvements to workspace-related system prompts.
- Improvements to the authoring skill.
- Fixed bug in chat that caused incorrect tool display after a failed or cancelled chat turn.
- CI now catches stray root-level markdown planning files before merge.
- Refactored monolithic app.js into smaller modules.

## 2026-06-06 - v0.6.4

### Workspace-aware chat sessions

Chat sessions can now be associated with a workspace folder inside the active vault.

- Set a workspace when you start or continue a chat so the default context setup can automatically load local orientation files.
- Add `README.md` to a workspace folder to warm the session with what the folder is about, current state, important files, goals, or constraints.
- Add `playbook.md` to a workspace folder for local working policy. It is merged after `AssistantMD/playbook.md`, so project-specific guidance can refine the vault-wide defaults without duplicating them.
- `session_ops` search boosts results if in same workspace as current session, and can filter search and list results by workspace.
- Refresh the system scripts manually from System / Misc to get the updated default context script.

### Activity log diagnostics

The System activity log is easier to inspect when diagnosing imports and runtime issues.

- Filter activity entries by search text, level, and tag.
- Ingestion now records per-file activity with the source filename, selected strategy, warnings, outputs, and OCR fallback details.
- Workflow scheduling and run activity now includes searchable workflow names, scheduler sync decisions, and terminal run details.
- Chat and context-template runs now record compact lifecycle summaries with stable event names, session/workspace identity, and terminal status, while noisy helper-level success details stay in validation artifacts.

### Provider reliability

- OpenRouter requests now use provider routing defaults that require requested parameters to be supported and skip Azure-backed routes by default. This is a trial workaround for observed provider-routing issues in long-running, tool-heavy chats rather than a guaranteed fix; the ignored provider list is editable in System settings.

## 2026-05-31 - v0.6.3

### Composable session summaries

Assistant.md now has session summaries as a composable long-term context building block.

- Prior chat sessions can be summarized into compact records with a summary, user intent, domain tags, and work product details, designed for retrieval and composition.
- The optional nightly workflow can maintain summaries over time, while context scripts, skills, and chat can decide when and how to use them.
- `session_ops` lets chat agents and authored scripts search prior summaries or create/update summaries through the same tool surface.
- Session summaries can be browsed from chat, edited when needed, included in manual transcript exports, and refreshed when they become stale.

### Safer chat history compaction

Chat history compaction is no longer destructive for newly compacted sessions.

- Prior releases rewrote the stored chat history during compaction. This release preserves the original transcript and uses the compacted summary plus retained recent messages for future chat turns.
- Session summaries and deep session search use the same compacted view that future chat turns see, so compacted sessions remain searchable without pulling in the full archived transcript.
- Compaction no longer creates transcript export files automatically. Transcript export is now only a manual UI action, which avoids unwanted files appearing in vaults.
- Purging chat sessions continues to clean up associated messages, tool events, compaction checkpoints, summaries, and transcript exports.

### Workflows and dashboard controls

Workflow management is more usable from the app UI.

- Workflows can now run as managed background tasks with status updates and cancellation support.
- Overlapping workflow runs are queued instead of racing each other.
- Built-in system workflows can be opened and edited from the dashboard.

### Shipped scripts and skills

The packaged defaults now provide a stronger starting point for composable vault behavior.

- Default context and built-in workflows now better support ongoing session notes and vault-first memory workflows.
- Packaged scripts and skills are easier to inspect, customize, and reuse in your own setup.

### Chat UI improvements

The chat workspace received several practical UI fixes.

- Added a focused chat workspace mode for a roomier writing and review surface.
- Improved mobile chat controls, modals, and focus-mode tapping.
- Improved LaTeX rendering.
- Other UI bug fixes.

### Database upgrades and maintenance

System database upgrades are now handled directly by Assistant.md.

- Registered database migrations run automatically on startup and create timestamped backups before changing existing databases.
- System / Misc now includes a Database Migrations panel that shows migration status and provides a manual fallback button.

### Other updates and fixes

- OpenRouter is now available as a built-in model provider; add your own OpenRouter model aliases in System settings.
- Extensionless file reads now resolve the intended markdown file before falling back to directory listing behavior.
- Vault mutation history and snapshot retention settings are now separate controls.
- Default tool settings were cleaned up to reduce unnecessary cache/tool noise.

## 2026-05-12 - v0.6.2

### Vault state, rollback, and incremental processing

Assistant.md now keeps a rebuildable vault-state index for every mounted vault, giving the app a clearer view of current files, recent changes, and Assistant.md-managed file mutations.

- Added stable vault identities, current file manifests, and change history in `system/vault_state.db`.
- Added scheduled whole-vault observation with the reserved `vault-state-refresh` system job, controlled by `vault_scan_interval_seconds`.
- File writes and deletes from chat, workflows, code execution tools, and ingestion now route through the vault-state mutation path where supported.
- Failed, cancelled, and timed-out chat/workflow tasks can automatically roll back supported file mutations.
- `pending_files(...)` now attaches diff metadata when a retained completion baseline is available, so incremental workflows can see what changed since the current scope last processed a file.
- Renamed the Workflows and Configuration tabs to Dashboard and System, with Dashboard UI improvements for vault activity, task mutation inspection, sortable workflow/activity tables, tracked file counts, latest vault changes, and retained snapshot links.
- Removed obsolete `code_execution_piston` and `internal_api` tool implementations.

## 2026-05-05 - v0.6.1

### Chat cancellation

Long-running chat responses can now be stopped cleanly.

- The chat UI now has a stop control while a response is running.
- Cancelled chats keep the submitted user message in the session history, without adding a partial assistant response.
- Cancellation works for both normal and streaming chat runs.

### Chat history compaction

Long chat sessions can now be compacted into a summary plus recent messages, helping keep future turns focused without losing the important context.

- Added a compact progress indicator under the chat prompt.
- Added a `chat_history_compact` tool so the assistant can compact the current session after explicit user approval.
- Compaction preserves recent tool call/result pairs and can export a transcript before rewriting the stored session history.
- New settings control whether compaction is disabled, suggested, or automatic, plus how much recent history is kept.

### Reliability improvements

- Workflow runs now share one coordination path across schedules, API runs, and the `workflow_run` tool.
- Workflow runs are guarded against overlapping execution within the same vault.
- Activity logging and validation coverage were expanded around chat cancellation, compaction, and workflow execution.

## 2026-04-30 - v0.6.0

### BREAKING: Markdown DSL replaced by Python authoring runtime

⚠️WARNING: This release replaces the markdown step-based authoring surface entirely with a Python-based authoring environment built on the **Pydantic Monty sandbox**. This affects every workflow and context template in your vault — all existing `.md` templates written using ## Step headings and @directives are now obsolete and must be migrated to the new format.

**Rationale:** The old authoring approach relied on a custom language which was becoming increasingly complex for both humans and LLMs to understand. Attempts to teach the chat agent to write automations were failing. Rather than invent a new language, this release leans into what LLMs already know how to do well - write code. Now you can describe the research / knowledge automation you want and the chat agent will create it for you.

**Safety:** This is not free-form Python. Authoring scripts run inside the Monty sandbox — a Python interpreter written in Rust with its own bytecode VM. Monty's default is zero access: no filesystem, no network, no environment variables, no arbitrary imports. The only way a script can interact with the outside world is through tools (e.g. `file_ops_safe`, `tavily_extract`) and host-owned helper functions (e.g. `retrieve_history`, `parse_markdown`). Each integration point is deliberate and auditable. The chat agent can write and run automation code on your behalf without any risk of it reaching outside the boundaries Assistant.md sets.

- **Workflows and context assembly scripts are now Python blocks.** Both live in a single `AssistantMD/Authoring/` folder — no more separate `Workflows/` and `ContextTemplates/` directories. Once you've migrated, you can delete the old folders.
- **Tools and helpers replace directives.** Authoring scripts use configured tools for host-owned access such as reading vault files or delegating model work, and focused helpers for authoring-specific operations such as `retrieve_history()`, `assemble_context()`, `pending_files()`, and `parse_markdown()`. Scripts still get normal Python control flow, conditionals, and loops instead of declarative DSL syntax.
- **`Skills/` is now a canonical vault folder.** Drop plain-text skill files there and the default context scripts picks them up automatically.
- **`soul.md` for simple customization.** Create `AssistantMD/soul.md` with plain instructions — agent personality, response style, ground rules — and the default template loads it as the system instruction. No template authoring needed for simple cases.
- **The chat agent can author and iterate scripts for you.** Describe what you want; the agent drafts the file, places it in `Authoring/`, and can compile and refine it with you. The documentation has been significantly simplified and reorganized so the agent can find what it needs without manual pointers.

### Pydantic AI capabilities refactor

Assistant.md's tools and hooks have been restructured to align with the architectural direction Pydantic AI is taking around **capabilities** as the primary extension point for reusable agent behaviour.

- A new `core/llm/capabilities/` package owns Assistant.md-specific capability implementations.
- Chat and authoring agent construction now assembles capabilities explicitly rather than threading tool lists and history processors through ad-hoc arguments.

### Chat session persistence and management

Chat sessions are now persisted in SQLite and survive app restarts.

- A **session picker** in the chat settings panel lists all stored sessions for the active vault; selecting one rehydrates the full conversation view.
- Session titles are editable inline in the picker and appear in exported transcript filenames.
- **Transcript export is now on-demand**: click the export action in the session picker. Transcripts contain only user and assistant turns; tool calls and returns remain in the database.
- Exported transcript files are preserved when a session is deleted.
- **Bulk session purge** is available under Configuration > Misc with vault and age-threshold selectors.

### Unified thinking controls

- A single thinking control surface now covers both chat and the authoring runner.
- Set per-run thinking level in the chat UI and default thinking level in Application Settings.

### Tool changes

- All tools are now enabled by default (except `web_search_duckduckgo).
- New `delegate` tool lets chat and authoring scripts hand bounded sub-tasks to a child agent, using the same configured tools and multimodal file-reading paths as normal chat.
- `file_ops_safe` interface changes:
  - `target` parameter renamed to `path`.
  - `scope` renamed to `search_term` (search semantics flipped: `path` is now the directory boundary).
  - New `frontmatter` operation (returns selected frontmatter keys).
  - New `head` operation (returns the first N lines of a file).
  - `read` and `head` now return the requested file content directly, without success-message wrapper text.
- Local db-backed cache is now used for oversized tool results that exceed context limits, replacing the former buffer.
- Manual cache purge controls added to Configuration.

### Other improvements and fixes

- Scheduler startup hardened against stale job-store references: if serialized jobs point to modules that no longer exist (e.g. after a package rename), the store is wiped and the scheduler retries clean. Jobs are always re-added from current workflow files on the same boot.
- Prerelease tags (e.g. `v0.6-beta`) no longer move the `latest` Docker image tag; `latest` is only updated by stable `vX.Y.Z` releases.
- Fixed LaTeX false-positive on currency values: `$10` no longer triggers inline math detection.
- Tweaked the chat context template fallback chain.
- Validation event filenames padded to 5 digits for correct sort order past 99 events.
- Global built-in authoring scripts are automatically upgraded on startup. Duplicate and rename if you want to customize any of the built-in scripts in `system/`.
- Integration test suite updated throughout to match the new authoring contracts.

## 2026-03-31 - v0.5.0.

### BREAKING CHANGE: new selector/filter structure for the `@input` directive

- The new mental model is: glob/file patterns select the candidate file set, `pending` or `latest` can filter that set, `order` sorts it, and `limit` is applied last. This allows greater flexibility. For example, previously, there was no way to fetch pending files in alphanumeric order - now there is.
- If your templates currently use substitution patterns `{pending}` or `{latest}`, you must update them.
- Old style:
  - `@input file: tasks/{pending:5}`
  - `@input file: journal/{latest:3}`
  - `@input file: projects/{latest}/notes.md`
- New style:
  - `@input file: tasks/* (pending, order=alphanum, dir=desc, limit=5)`
  - `@input file: journal/* (latest, limit=3)`
  - `@input file: projects/*/notes.md (latest, order=ctime, limit=1)`
  - `@input file: inbox/*.md (order=mtime, dir=desc, limit=10)`

### Added enable / disable operation to `workflow_run` tool

- You can now manage workflow state through the `workflow_run` tool with:
  - `enable_workflow`
  - `disable_workflow`
- Your workflows can include a step that disables the workflow when a condition is met so they don't run forever. Chat can also enable/disable workflows.
- **BREAKING CHANGE**: Previously, `enabled=true` was optional. If a schedule was present and `enabled` was missing, it would default `true`. New workflows now default to `enabled: false` if missing. If you create or copy in a workflow and expect it to start running on its schedule immediately, you will need to enable it explicitly.

### New tool: `browser`

- Added a Playwright-backed `browser` tool for extraction from known URLs when simple web extraction fails or pages depend on JavaScript.
- Intended usage order is: search first, `tavily_extract` second, `browser` as the heavier fallback.
- Browser policy is intentionally narrow:
  - downloads blocked
  - local/private network blocked, including redirects and subrequests
  - only read-oriented requests (`GET`/`HEAD`)
  - stateless per call
- Added browser-specific settings:
  - `browser_navigation_timeout_seconds`
  - `browser_selector_timeout_seconds`

### Other improvements

- Strengthened prompt-injection guidance for web tools so suspicious web content is treated as untrusted data and attacker strings are less likely to be echoed back verbatim.
- Chat now surfaces known model capability mismatches, such as attaching images to a non-vision model, as explicit client errors with actionable guidance instead of generic network/internal failures.
- Hardened chat session error handling, especially for streaming execution, so unexpected failures now leave structured `activity.log` diagnostics with session context, execution phase, exception type, and traceback information.
- Simplified the Workflows dashboard by making the main sections collapsible and unifying scheduled and unscheduled workflows into one clearer table with status and next-run information.
- Refreshed frontend/build dependencies to address npm vulnerability warnings, switched setup to `npm ci` for lockfile-based installs, and added dependency audit checks in CI.
- Documentation updates

## 2026-03-27 - v0.4.3.

### Added images as first-class input type

- Images are supported in chat, workflow and context templates. E.g. `@input file: myimage.png`
- `file_ops_safe(read)` supports image reads and markdown files that contain embedded images.
- Markdown files with embedded images are read in source-order so the LLM sees content as it appears in the document (text and images are interleaved into a multimodal prompt).
- When images cannot be attached (for example due to model or size limits), Assistant.md falls back gracefully with clear, followable image references instead of failing.
- Added image attachment size controls in settings: `chunking_max_image_mb_per_image`, `chunking_max_image_mb_total`, `chunking_max_images_per_prompt`.
- For markdown files with embedded images, Assistant.md preflights raw text token size first; if text alone exceeds `auto_buffer_max_tokens`, it skips multimodal attachment, returns text with normalized image reference markers and standard auto-buffer routing can apply.
- PDF import includes a page-image mode that outputs each page as an image, useful for documents where standard markdown conversion fails to output useful information.
- Import supports image-source OCR flows, including optional capture of OCR image assets in import outputs.

### Bug fixes

- Fixed inconsistent `@model none` handling across context and workflow execution. Steps/sections configured to skip now reliably bypass LLM execution instead of partially entering model setup paths.
- Fixed invalid model configuration handling so chat/default-model execution cannot proceed with skip-mode aliases like `none`; the app now raises a clear configuration error.
- Fixed directive date/time format token replacement so expanded values are not mutated by overlapping tokens (for example weekday/month text no longer gets corrupted by single-letter token passes).
- Fixed validation artifact consistency so scenario `timeline.md` outcomes now align with CLI pass/fail results (including explicit final outcome markers and teardown on failure paths). (Issue #28)

### Validation Scenario Refactor

- Organized validation into two lanes: `integration/core` for deterministic CI and merge-gate contracts, and `experiments` for live, external, stress, and diagnostic scenarios.
- Consolidated overlapping contract coverage into core scenarios (especially `primitives_contract`) and retired redundant overlap cases.

### Documentation Updates

- Split contributor/agent guidance into progressive-disclosure docs under `docs/agent-guides/`, with a simplified root `AGENTS.md`.
- Added a running refactor checklist in `validation_suite_refactor_plan.md` and aligned validation documentation to the new scenario structure.

## 2026-02-25 - v0.4.2.

### Bug fix: OpenAI-compatible provider auth and base URL wiring

- Fixed OpenAI-compatible provider setup to consistently pass configured `api_key` and `base_url` values (from secrets or literal settings).
- Unified OpenAI-compatible routing to use `OpenAIProvider` so both authenticated remote endpoints and local no-auth endpoints work through the same path.
- Added custom `base_url` support for the `openai` provider configuration path.
- Fixed chat streaming assembly to include only visible text parts, preventing reasoning/thinking part prefixes (for example stray leading words like `"The"` in some provider streams).
- Allowed base-url-only OpenAI-compatible providers (for example local LM Studio without API key), so local endpoints are usable when `base_url` is configured.
- Updated configuration health warning logic to only warn when no LLM provider/model is usable, instead of warning whenever no API key exists.

## 2026-02-24 - v0.4.1.

### Feature: LaTeX rendering in chat

This release adds first-class LaTeX rendering in assistant responses using bundled MathJax in the chat UI.

- Supports inline math (`\(...\)`) and display math (`\[...\]`).
- Preserves math while markdown is parsed, then typesets math after render (including streaming responses).
- Skips math parsing inside code blocks/inline code so examples stay literal.

### Security and rendering hardening

- Added DOMPurify to sanitize assistant-rendered HTML before inserting into the chat UI.
- Improved post-processing flow for assistant messages so link behavior, math rendering, and code-copy buttons are applied consistently.

### Chat instruction stack simplification

- Removed request-level custom chat instructions override from the chat API path.
- Consolidated default chat behavior into the regular instruction template/constants for more predictable prompting.

### Documentation and legal

- Added `THIRD_PARTY_NOTICES.md` with bundled frontend asset notices and dependency inventory references.
- Updated README links for reference docs, license, and third-party notices.

## 2026-02-20 - v0.4.0.

### Feature: Context manager

This release introduces the **Context Manager** which allows you to shape what the chat agent sees, from simple system‑prompt injection to multi‑step context assembly. It applies the lessons learned by research on long‑running agents: curated working sets, structured summaries and explicit attention budgeting beat dumping full transcripts into ever‑larger contexts.

It is template‑driven and step‑based, with explicit controls for how history is curated and optional caching/observability; see the docs for full details on directives, gating and persistence.

### Feature: Buffer (virtualized I/O)

The buffer is an in-memory key-value store that the chat UI, context templates and workflows can use to temporarily store data. Entries in the buffer are called variables. The buffer is useful for passing data between steps in a context or workflow template, or to avoid blowing up the context window with huge tool outputs.

A new `buffer_ops` tool allows the LLM to access buffer variables systematically. This feature is the first step toward enabling a robust [RLM-style approach](https://alexzhang13.github.io/blog/2025/rlm/) to context management.

### Additional features

- Added `@input (...properties...)` mode to inject frontmatter properties instead of full file content.
- Added formatted time patterns for directives.
- Added `workflow_run` tool support in chat to list and execute workflows from the active vault.

### Breaking changes

- **Directive rename**: `@input-file` → `@input`, `@output-file` → `@output` (no backward compatibility).
- **Scheme-based targets**: `@input` / `@output` now require explicit targets (`file: ` / `variable: `).
- **Parameter rename**: `paths-only` → `refs-only` for `@input` (no backward compatibility).
- **Tool deprecation**: Removed `import_url` and `documentation_access` tools (assisted template creation is now handled using the context manager).

### Documentation

- Significant documentation updates.
- New library of example context and workflow templates.
- LLM can read documentation with file_ops_safe using virtual path root `__virtual_docs__/`.

### Chores

- Upgraded `pydantic-ai` to `1.60.0` and refreshed the lockfile.
- Hardened release workflow trigger logic and removed changelog dependency from CI release flow.
- Enforced lint/tooling hygiene and cleanup across context/template execution paths.

### Bugs / Fixes

- Chat UI now preserves selected vault/model/template/tools across metadata refreshes.
- Vault selector is locked to the active chat session to prevent mid-session vault switches, with clearer tooltip guidance.
- Assistant message links now open in a new tab to avoid disrupting current session.
- Hardened `file_ops_safe` search: configurable timeout, case-insensitive matching, safer scope boundary checks, and normalized result paths.
- Hardened vault path validation against symlink-based escape paths.
- Improved template-facing error surfacing in context manager/workflow execution.
- Standardized quoted-comma directive parameter handling to reduce parsing edge-case failures.

## 2026-01-24

This release refactors the UnifiedLogger and parts of the validation framework.

### UnifiedLogger

- Refactored logging to a sink-based model
- Added one-shot sink overrides in the form `logger.add_sink().info()` / `logger.set_sinks().warning()`
- Added new validation sink that logs to yaml files only during validation runs.
- Removed redundant trace decorator

### Validation framework

- Updated all integration scenarios to use the new validation sink logs to test internal state and removed tightly coupled helpers
- Removed all custom assertion helpers and refactored scenarios to use regular python assert statements
- Improved coverage of several integration scenarios
- Tools now emit tool_invoked validation events
- Overall reduction in surface area of the validation framework, slowly moving it toward a generic validation platform

## Other

- App runtime now assigns unique boot_id on each restart
- Review and cleanup of activity.log calls: dedupe, reduce noise and identify logging gaps
- Removal of lingering code from various deprecated features (e.g. chat compact endpoint, workflow creation endpoint, session type switching)
- Tools were normalized to pydantic_ai.tools.Tool
- Docs updated to reflect changes to logging and validation

## 2025-12-08

### Feature: Import to markdown pipeline

- Import PDF using pymupdf and optional Mistral OCR (with API key)
- Import URLs
- Ingestion settings and UI controls
- URL import accessible via LLM tool call
- Validation scenario for coverage
- **Note**: The importer is a work in progress and likely to change.

### Feature: Repair settings.yaml

- Warning in the UI if settings are missing from system/settings.yaml and provide repair tool
- Existing setting are unchanged
- Settings.yaml is backed up to system/setting.bak before repair

### Refactor

- Consolidated redundant metadata APIs
- Hardened runtime path helpers, now require bootstrap/runtime context (no env fallbacks), entrypoints seed bootstrap roots early, secrets store uses a single authoritative path, and validation harness aligns with the same bootstrap rules.
- Logger/bootstrap safety: logfire configuration now defers when settings/secrets aren’t available during early imports to avoid startup crashes.
- Update docs

### Breaking change

- Custom scripts/entrypoints must call `set_bootstrap_roots` (or start a runtime context) before importing modules that resolve paths/settings; secrets overlay merging was removed in favor of a single `SECRETS_PATH` or `system_root/secrets.yaml`.

## 2025-11-29

- Runtime path resilience: renamed system root env/fields to `CONTAINER_SYSTEM_ROOT`/`system_root`, added `core/runtime/paths.py` helpers, and routed settings/secrets/logger/DB/workflow loaders through runtime-aware path resolution.
- Validation secrets handling: validation now uses the real secrets file via path helpers (no per-run copies), and removed teardown unlink to avoid deleting secrets. Devcontainer env updated to set `CONTAINER_SYSTEM_ROOT` alongside `CONTAINER_DATA_ROOT`.
- Doc update: architecture quick reference now lists the new path helper module.
- `.gitignore` now ignores the entire `system/` directory to keep runtime artifacts out of git.
- **Breaking change (env overrides):** If you previously set `SYSTEM_DATA_ROOT` to override the system path, update to `CONTAINER_SYSTEM_ROOT`. The default paths (`/app/data`, `/app/system`) are unchanged. Any custom devcontainer/env settings should switch to the new names.
