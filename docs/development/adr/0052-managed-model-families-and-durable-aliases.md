# 0052 - Manage Model Families Through Durable Aliases

## Status

Accepted. Complements [ADR 0007](0007-settings-backed-model-and-tool-binding.md).

## Context

Provider model names and versions change faster than scripts, settings, and saved chats. AssistantMD needs to keep those references usable while maintaining a clear distinction between application-managed model families and user-owned custom bindings.

## Decision

Model aliases are the public selection contract. Provider model identifiers sit behind those aliases and can evolve without requiring changes to every script or saved setting.

The application owns managed model families, including their primary aliases and provider bindings. Stable catalog identities let these entries survive renames and upgrades. Provider-supported latest aliases are preferred where available.

Each model has one primary alias and one ordinary editable list of alternate aliases. Primary-alias renames preserve the previous alias in that list. All aliases resolve to the same binding and capabilities.

Standard aliases with matching providers and model families become managed. Custom aliases remain user-owned. Conflicting bindings are preserved and surfaced for explicit resolution.

Managed aliases track evolving model families rather than fixed versions. Custom bindings provide independent control. Model management does not introduce tier-based routing or fallback chains.

## Consequences

- Authored references can survive model renames without editing every script or saved setting.
- Managed aliases track evolving model families and do not promise a fixed underlying model version.
- Custom bindings provide independent control and require user-managed upgrades.
- Alternate aliases remain simple and user-editable; removing one can invalidate references that still use it.
- Conflicts remain visible rather than silently replacing a different binding.

## Evidence

- `core/settings/model_catalog.py`
- `core/settings/model_registry.py`
- `core/settings/config_editor.py`
- `core/chat/model_selection.py`
- `validation/scenarios/integration/core/model_alias_resolution.py`
- `validation/scenarios/integration/core/chat_model_alias_fallback.py`
