# 0042 - Use the Advanced Shell for Stdio MCP and Advanced Execution

## Status

Accepted.

## Context

MCP's network transports and stdio transport have materially different runtime
requirements. A Streamable HTTP or SSE connection reaches an independently
running server over the network. AssistantMD owns the client connection but
does not install, start, or supervise the server process.

A stdio connection instead requires AssistantMD to start an executable and own
its stdin, stdout, termination, and process-tree cleanup. Providers may require
their own runtimes, packages, writable files, and operating-system tools. Running
those third-party processes directly in the AssistantMD application container
would expand its dependency surface, give provider code access to application
runtime state, and couple provider lifecycle and resource use to the web and
agent backend.

The advanced shell supplies a separate, constrained Linux execution environment
with persistent package and workspace storage. It was selected as the execution
boundary required for stdio MCP providers. Once that general Linux environment,
fixed SSH transport, lifecycle control, and containment boundary existed, it
also provided the missing foundation for general agent shell access.

Using the same environment for both purposes avoids maintaining one execution
stack for provider processes and another for interactive commands. It also
preserves the restricted AssistantMD application image and makes broader
execution an explicit deployment choice rather than an ambient capability of
every installation.

## Decision

Treat network MCP servers and stdio MCP providers as distinct execution
architectures behind one principal-owned MCP connection and tool-consumption
model.

Streamable HTTP and SSE connections target independently managed services.
AssistantMD applies network policy and attaches supported bearer,
custom-header, or OAuth credentials from its encrypted principal-owned secrets
store.

Run stdio MCP providers only in the advanced shell, never directly in the
AssistantMD application environment. AssistantMD opens a deployment-owned SSH
connection and sends a versioned structured launch containing a fixed
executable path, literal arguments, a working directory, and bounded non-secret
environment values. The advanced-shell forced-command wrapper starts the
provider without shell parsing and owns its complete process-tree cleanup.
Connection metadata cannot select another SSH destination, user, identity, or
host-key policy.

Expose general shell access through this same advanced-shell boundary when the
deployment explicitly selects advanced execution mode. Keep restricted mode as
the default and do not install a general shell execution path inside the
AssistantMD application container. General shell calls and stdio sessions may
share the advanced-shell Unix identity and persistent environment, but they
retain distinct protocols, lifecycles, result handling, and tool-governance
contracts.

Resolve advanced-shell access behind execution authority rather than treating a
global SSH credential as principal authority. The initial single-user product
resolves interactive `local-user` work to one advanced shell. The resolver seam
can later select distinct Linux users or distinct containers if a multiuser
deployment architecture is adopted; this decision does not select either
strategy.

Require advanced execution mode for stdio configuration, testing, and runtime
acquisition. Reuse the ordinary MCP manager, principal ownership, immutable
connection slugs, tool allowlists, frozen catalogs, call budgets, result
shaping, invalidation, and lease lifecycle after crossing the transport-specific
launch boundary.

The general `shell` tool and stdio providers intentionally share the advanced
shell's Unix identity and persistent environment. This makes user-installed
packages and configuration available to stdio providers, but it also means the
environment is agent-accessible.

Users may deliberately store credentials there for stdio providers, but those
credentials are readable and usable through `shell` and by other same-user
processes. Environment injection, files, pipes, inherited descriptors, or an
encrypted launch package cannot make a credential available to a same-user
provider while reliably concealing it from the agent. The structured stdio
contract therefore does not integrate with AssistantMD's encrypted secrets
store or claim protected secret delivery.

When credentials must remain outside the agent-accessible environment, run the
provider as an independently managed Streamable HTTP or SSE MCP service. A
future protected local runner would require another execution identity and
trust boundary and is a separate architecture decision.

## Consequences

- Third-party stdio providers and their dependencies stay outside the
  AssistantMD application container.
- Stdio support depends on advanced-mode deployment, SSH readiness, advanced-shell
  resource limits, and complete cross-process cleanup.
- General shell access and stdio providers reuse one separately contained Linux
  environment rather than expanding the AssistantMD application image or
  creating parallel execution stacks.
- Restricted deployments do not start or expose the advanced execution
  capability.
- The advanced shell can support user-installed runtimes and packages without
  adding them to the AssistantMD image.
- Network and stdio transports have different configuration, authentication,
  availability, and launch checks while converging on the same model-facing MCP
  tool contract.
- The general shell capability can inspect and modify the same persistent
  environment used by stdio providers. Provider installation is therefore an
  explicit trust decision.
- AssistantMD does not enforce a credential-free shell. Credentials placed
  there by users are part of the agent-accessible environment.
- AssistantMD-managed encrypted credentials remain available to network MCP
  transports but are not copied into stdio launch metadata or the advanced
  shell.
- A stdio provider that requires credentials must either use credentials the
  user knowingly places in the shell or be deployed as an independent network
  MCP service when that exposure is unacceptable.

## Evidence

- Current system map: `docs/development/architecture.md`
- Advanced-shell operational and security boundaries: `docs/tools/shell.md` and
  `docs/setup/security.md`
- Execution-mode and authority integration:
  `ADVANCED_MODE_SHELL_IMPLEMENTATION_PLAN.md`
- Principal-owned MCP connection model: ADR 0035
- Encrypted principal-owned secrets: ADR 0034
- Advanced-shell stdio launch boundary: `core/advanced_shell/stdio.py` and
  `docker/advanced-shell/forced_command.py`
- Runtime client and lifecycle ownership: `core/mcp/manager.py`
