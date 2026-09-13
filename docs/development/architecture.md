# Architecture Overview

AssistantMD is a single-user, Markdown-first agent system with a Python backend, FastAPI API and UI, durable vault and system state, and several bounded execution environments. This document is a map of the current system, not an exhaustive inventory of classes, schemas, or endpoints.

Code is authoritative for implementation. [Architecture decision records](adr/) explain why durable boundaries were chosen. User behavior belongs in `docs/use/` and `docs/tools/`; deployment and security guidance belongs in `docs/setup/`.

## System map

```text
Browser
  │ authenticated HTTP and task event streams
  ▼
FastAPI application (main.py, api/, static/)
  │
  ▼
Runtime composition root (core/runtime/)
  ├── chat sessions and execution tasks
  ├── authoring loader, workflow governor, and scheduler
  ├── model and capability resolution
  ├── ingestion and vault-state services
  ├── principal-owned connections and encrypted secrets
  └── activity, history, summaries, and goals
        │
        ├── Monty sandbox with explicit host capabilities
        ├── built-in and connection-backed tools
        ├── retained network MCP clients
        └── fixed SSH transport to the optional advanced shell

Persistent state
  ├── data root: user vaults and AssistantMD-authored Markdown
  ├── system root: settings, databases, logs, caches, and snapshots
  └── advanced-shell volumes: home, workspace, and SSH pairing state
```

The web UI calls the same API and runtime services used by programmatic clients. Chat, direct workflow runs, and scheduled workflows converge on shared execution, authority, capability, activity, and persistence services rather than owning parallel implementations.

## Runtime and execution

`main.py` resolves data and system roots before importing path-sensitive modules, constructs infrastructure configuration, and creates the FastAPI application. The application lifespan calls `core/runtime/bootstrap.py`, which migrates and opens managed state, validates configuration, builds shared services, synchronizes scheduled work, and publishes one process-wide `RuntimeContext` composition root. Mutable request or task identity is context-local and is never stored globally in that runtime object.

Interactive chat enters through `core/chat/`. A session has an immutable owner and workspace. A process-local execution task owns the model stream, tool-call state, cancellation, buffered event replay, and safe recovery checkpoints. Agent construction resolves the selected model, context instructions, effective tools, connections, limits, and output handling for the captured execution authority. Deferred inline-edit review resumes through the same task-owned execution path.

Ordinary browser-facing tool lists and lifecycle events expose only tool identity, lifecycle state, and estimated result size. Persisted tool arguments and results cross into the browser through the authenticated per-call detail endpoint after an explicit user request; those responses are non-cacheable and the UI clears its detail references when the modal closes. Deferred-review cards are the deliberate exception: they receive proposed tool arguments because the user must inspect and may edit them before approving execution.

Authoring files under `core/authoring/` define Markdown workflows and context assembly backed by Python executed in the Monty sandbox. Monty code receives only explicit host capability functions; it does not inherit the application process or unrestricted Python access. The workflow governor applies vault lanes, timeouts, cancellation, authority propagation, activity, and durable run history. APScheduler jobs under `core/scheduling/` retain the workflow owner and enter the same governed path.

Execution tasks are process-local coordination objects. Domain outcomes such as chat history, workflow runs, vault activities, goals, and connection state are persisted by their owning subsystems. A process restart may end active work, but must not make transient task snapshots the canonical record of completed work.

## Identity, authority, and trust

AssistantMD currently resolves one interactive `local-user` principal and one reserved `system` principal. The distinction is already enforced in persistence and runtime services so authentication can resolve more principals later without another ownership migration.

Ingress authentication proves that an HTTP caller may resolve to a principal. Execution authority controls access to principal-owned resources after that authentication. Proxy assertions, owner tokens, browser sessions, OAuth state, SSH keys, task IDs, and model arguments are not execution authority.

Chat sessions, workflow definitions, schedules, workflow runs, tasks, secrets, and connections carry explicit ownership. Request handlers install authority; background workers reinstall the authority captured when work was created. Missing authority fails closed rather than defaulting to the interactive user.

The major trust boundaries are:

- **Browser ingress:** deployment-selected authentication wraps the complete ASGI application. Only explicitly classified routes are public.
- **Vault access:** vault services enforce path containment and route mutations through durable activity, revision, and snapshot infrastructure.
- **Monty:** user-authored Python receives a constrained interpreter and declared host capabilities rather than application-process access.
- **External content:** web pages, email, imported documents, model output, and MCP server descriptions/results are untrusted data even when fetched through an authenticated connection.
- **Advanced shell:** arbitrary commands run in a separate container reached at a deployment-controlled SSH destination. Docker isolation and explicit mounts are the containment boundary; a writable mount grants raw filesystem authority outside AssistantMD's principal services.
- **External services:** network policy, encrypted credentials, connection ownership, bounded clients, and sanitized results mediate access. Configuring a service is an explicit trust decision, not an assertion that its tools are safe.

## Capability model

Capabilities are assembled for the current execution authority rather than registered indiscriminately on every agent.

| Category | Examples | Availability and disclosure |
| --- | --- | --- |
| Built-in tools | vault reads, writes, web, workflows | Settings-backed; generally first-class when enabled |
| Connection-backed native tools | Gmail | Available only when the principal has a usable, sufficiently scoped connection |
| Network MCP tools | Streamable HTTP and SSE servers | Interactive primary chat only; principal-owned retained clients are filtered, prefixed, and individually deferred behind tool search |
| Stdio MCP tools | Providers launched in the advanced shell | Interactive primary chat only; the same MCP governance applies after a structured, bounded SSH launch |
| Monty host capabilities | functions used by authoring scripts | Explicitly supplied to the sandbox for the current workflow or context run |
| Advanced shell | general `shell` tool | Interactive primary chat only, when advanced mode, authority, and SSH readiness permit it |
| Delegation | bounded child agents | Parent-owned execution with explicit tools, limits, and failure handoff |

Built-in tool configuration lives under `core/tools/`, shared binding under `core/authoring/shared/`, and model-facing composition under `core/llm/` and `core/chat/`. Connection gating happens before a tool reaches an agent. Global tool disablement remains authoritative after connection-specific readiness.

MCP connections are principal-owned records with immutable IDs and readable slugs. Remote tool names are prefixed with the slug so persisted model history continues to identify the same connection after display-name edits. Retained runtime clients settle a frozen catalog for each run; one unavailable server does not remove unrelated capabilities. Deferred discovery reduces initial context exposure but does not make an untrusted server catalog trustworthy.

Stdio definitions contain a structured executable, literal arguments, working directory, and bounded non-secret environment rather than a shell command. The advanced-shell decoder enforces a 64 KiB request limit and requires working directories to resolve below `/workspace` or `/home/advanced-shell`. Only `local-user` may configure or launch the supplied single-user stdio boundary. `mcp_max_concurrent_advanced_shell_stdio_launches` limits concurrent managed cold launches, while container PID, memory, and CPU limits remain the aggregate bound for stdio and direct shell processes.

The advanced shell and stdio providers share one Unix identity and persistent environment. Credentials deliberately placed there are therefore readable by the chat agent and other same-user processes. AssistantMD-managed credentials are not injected into that environment.

## Data and storage ownership

The configured data and system roots are persistent runtime state. Subsystems own their databases and expose service boundaries rather than sharing tables through ad hoc SQL.

| State | Authority and source of truth | Location |
| --- | --- | --- |
| Vault content | Canonical user-owned Markdown and related files | data root |
| Authoring catalog | Managed and project-local Markdown definitions | vaults beneath the data root |
| Installation settings | Deployment-wide typed configuration | system root and restart-bound environment |
| Encrypted credentials, OAuth state, MCP and native connections | Separate principal-owned domain tables sharing atomic mutations | `access.db`; credentials use the external installation key |
| Chats and messages | Principal-owned canonical conversation state | chat subsystem databases |
| Workflow outcomes | Principal-owned durable domain history | workflow-run database |
| Vault activity and recovery | Attributed activities, revisions, and snapshots | vault-state databases and snapshot storage |
| Session summaries | Rebuildable derived memory indexes | memory subsystem state |
| Goals | Lightweight durable state with provenance | goals subsystem state |
| Active execution coordination | Task owner, events, cancellation, checkpoints | bounded process memory |
| Advanced-shell files | Deployment shell user; agent-accessible | Docker home and workspace volumes |
| SSH pairing | Container-owned disposable infrastructure identity | dedicated Docker volumes |

Cross-database operations do not pretend to be atomic. Where one logical connection mutation spans metadata and encrypted secrets, the owning service uses durable sanitized intent, lifecycle fencing, idempotent reconciliation, and runtime invalidation. Credential generations and deletion records prevent stale OAuth responses or leftover ciphertext from restoring invalid authority.

Vault files remain the durable user-data contract. Databases may index, coordinate, or record operational history, but derived indexes must remain rebuildable and must not quietly become the only copy of user-authored knowledge.

## Content and retrieval flows

The ingestion subsystem separates source loading from extraction strategy. A source produces a typed raw document; an extraction strategy selects native text, OCR, transcription, or another transformation; rendering and storage then enter the normal vault-mutation boundary. Every accepted import is represented by a durable job. Interactive API and tool calls can process those jobs immediately, while queue-only submissions are claimed by the background worker; both paths share execution-task attribution, durable status, and output handling.

Web retrieval exposes stable model-facing capabilities while provider strategies remain replaceable below them. Network transports enforce URL and address policy, bounded responses, explicit timeouts, and normalized results.

Multimodal input is admitted only when both product policy and the selected model support it. Image markers and attachment references are normalized before model requests; chunking and buffering keep large payloads out of ordinary text context.

Session summaries are derived memory, not canonical chat history. Goals are a small durable coordination ledger, not a second workflow or task system.

## Where new work belongs

Most features have one domain owner and several integration surfaces. Put policy and business behavior in the owning domain; keep model tools, APIs, UI code, and schedulers as adapters over that behavior.

| Change | Domain owner | Integration surfaces |
| --- | --- | --- |
| Model provider or model configuration | `core/llm/` | Provider settings, secrets, model status, and agent construction |
| Model-facing built-in tool | Service for the domain being exposed | Thin `core/tools/` adapter, capability composition, and tool documentation |
| Native authenticated service | `core/integrations/<provider>/` | `core/connections/`, `core/oauth/`, secrets, and thin tool/API/UI adapters |
| MCP transport or lifecycle behavior | `core/mcp/` | Shared OAuth, connection UI, runtime ownership, and capability composition |
| Tool selection or capability policy | `core/llm/capabilities/`; settings-backed binding currently lives in `core/authoring/shared/` | Chat, Monty, delegates, workflows, settings, and connection readiness |
| Authoring capability | `core/authoring/` host boundary | Monty runtime, tool binding, workflow governor, and user-facing authoring reference |
| Workflow semantics | `core/authoring/` and `core/runtime/workflow_governor.py` | Durable run history, execution tasks, and scheduling |
| Scheduling mechanics | `core/scheduling/` | Workflow ownership, governor admission, task execution, and run history |
| Imported source type | `core/ingestion/sources/` | Existing raw-document, extraction-strategy, job, and vault-mutation contracts |
| Vault mutation | `core/vault_state/` | Domain service requesting the change, activity, revisions, snapshots, and UI/API adapters |
| Ingress authentication | `core/authentication/` | FastAPI application boundary, browser session endpoints, and deployment settings |
| Advanced execution | `core/advanced_shell/` | Shell tool, stdio MCP adapter, SSH wrapper, Docker image, and Compose topology |
| Persistent subsystem state | Subsystem that owns the records | `core/database.py`, subsystem schema migration, recovery, and validation |
| API or UI presentation | Core service for the represented domain | Thin `api/` orchestration followed by `static/` presentation |

When a change alters ownership, authority, trust, persistence, or a major cross-subsystem flow, update this overview and add or amend an ADR. Ordinary endpoint, schema-field, helper, and class changes should be documented by code, tests, and the relevant user or operator guide rather than expanding this page.

## Decision records

ADRs under [`docs/development/adr/`](adr/) are append-oriented records of durable decisions and their consequences. They explain why the current system took its shape; this overview describes that shape now. An ADR may remain historically valuable after implementation details change, so current behavior must not be inferred from one ADR in isolation.

Start with these groups when investigating a boundary:

- runtime, tasks, chat, and workflows: ADRs 0001–0004, 0014, 0019–0020, 0026, 0028, and 0031–0033;
- vaults, ingestion, memory, and goals: ADRs 0005–0006, 0011–0018, 0024–0025, and 0029–0030;
- tools, models, and external capabilities: ADRs 0007–0010, 0021–0023, 0027, 0035, 0037, 0039, 0042, and 0045;
- identity, storage, connections, and OAuth: ADRs 0015, 0028, and 0034–0041;
- deployment and advanced execution security: ADRs 0036 and 0042–0044.

For contributor setup, commands, validation ownership, and production-shaped branch deployment, see the [Development Setup Guide](dev-setup.md).
