# 0022 - Treat OpenAI OAuth As Experimental Auth Boundary

## Status

Accepted.

## Context

AssistantMD supports the built-in OpenAI provider through the normal OpenAI
Platform API-key path. OpenAI Platform API keys are the documented stable path
for programmatic API use.

This branch adds a Codex/ChatGPT-compatible OpenAI OAuth path so a single-user
AssistantMD install can connect OpenAI chat models without storing a Platform
API key. That OAuth path has a different support profile from API keys: token
shape, endpoint behavior, refresh behavior, and policy compatibility can change
outside AssistantMD's control.

OAuth also introduces new secret state. Connected token state and pending auth
attempts must survive restarts, but token material must not become normal
provider config, appear in generic secrets lists, or be logged.

## Decision

Treat OpenAI OAuth as an experimental auth path behind an explicit boundary.
OpenAI Platform API-key auth remains the stable default and recommended
production path.

The OpenAI OAuth boundary has these rules:

- OAuth applies only to the built-in `openai` provider.
- OAuth is controlled by the global `openai_oauth_enabled` setting. When that
  setting is false, OpenAI resolves as API-key-only even if OAuth token state is
  present.
- OAuth token state and pending auth state are stored as internal secrets, not
  normal provider settings.
- Generic Secrets UI/API surfaces hide internal OAuth secret entries. Provider
  APIs expose only sanitized OAuth status and account metadata.
- Runtime model construction must resolve OpenAI auth through the dedicated
  OpenAI auth/runtime boundary instead of treating OAuth tokens as API keys.
- API-key fallback from OAuth mode is explicit opt-in through
  `oauth_api_key_fallback_enabled`.
- If OAuth is selected but unavailable, and fallback is not enabled, runtime
  model construction fails with guidance to reconnect OAuth or switch auth mode.

## Rationale

This keeps the stable OpenAI API-key setup reliable while still allowing users
to experiment with OAuth where it works for their deployment.

The global kill switch gives maintainers and users a low-risk escape hatch if
OpenAI changes OAuth behavior. Keeping token state as hidden internal secrets
preserves restart durability without turning OAuth material into ordinary
configuration. Requiring explicit API-key fallback avoids surprising users by
silently switching auth or billing paths after an OAuth failure.

The runtime boundary also protects future maintenance. If OAuth requires
different endpoint handling, headers, request shaping, refresh rules, or
provider adaptation, those changes stay inside OpenAI-specific code instead of
leaking into general model/provider resolution.

## Consequences

- Users should still prefer `OPENAI_API_KEY` for the most stable OpenAI setup.
- OpenAI OAuth can be disabled without deleting stored OAuth state.
- The UI can show connection, pending, disabled, expired, and reconnect-needed
  states without exposing tokens.
- OAuth failures are visible and actionable instead of falling through to
  another auth path by default.
- Other providers and generic OpenAI-compatible providers do not inherit OAuth
  behavior.
- Future OAuth compatibility work should preserve this boundary unless the
  support contract changes enough to make OAuth a stable primary path.

## Evidence

- Current system map: `docs/development/architecture.md`
- Implementation plan: `openai-oauth-plan.md`
