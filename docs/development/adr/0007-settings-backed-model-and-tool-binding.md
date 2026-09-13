# 0007 - Use Settings Backed Model And Tool Binding

## Status

Accepted, backfilled.

## Context

Chat agents, delegate child agents, and authored scripts all need to resolve
models and tools. Providers need secret values, but those values must not live
inside normal settings metadata or documentation.

## Decision

Use typed settings as the registry for model aliases, provider wiring, and tool
availability. Keep confidential values in the secrets store and reference them
by pointer from provider or tool settings. Use shared tool-binding and model
resolution paths for chat, delegate, and authoring.

## Rationale

Settings-backed binding makes capability availability explicit and reloadable.
It also gives the UI/API one contract for editing models, providers, general
settings, and tool registry entries. Separating secrets from settings keeps
configuration reviewable while avoiding populated secret values in normal
repository or product docs.

## Consequences

- Built-in config changes should update the settings template.
- Runtime reload must refresh settings, model, config-status, and logging
  caches together.
- Tool modules do not become available merely by existing in `core/tools`; they
  must be present in the settings-backed registry.
- Secret names are metadata; secret values live in `system/secrets.yaml` or the
  configured secrets path.

## Evidence

- Current system map: `docs/development/architecture.md`
- Recovered sources: PR #40 `authoring_architecture_plan.md`,
  `DELEGATE_TOOL_IMPLEMENTATION_PLAN.md`,
  `pydantic-ai-capabilities-refactor-plan.md`
