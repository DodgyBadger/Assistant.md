# 0021 - Keep Provider Reasoning Transient By Default

## Status

Accepted.

## Context

Some model providers expose reasoning or thinking artifacts as provider-native
message parts. Pydantic AI represents these as `ThinkingPart` entries inside
assistant `ModelResponse` messages. Persisting those parts in canonical chat
history means they are replayed on later turns.

That creates two problems. First, reasoning parts can add recurring token cost
without improving the user's durable conversation record. Second, provider
reasoning formats are not portable: a session that contains reasoning output
from one provider may fail or behave unpredictably when replayed through a
different provider.

Users may still want to inspect thinking output during a live turn, especially
for long or tool-heavy runs, and advanced users may intentionally choose to keep
provider-native reasoning artifacts.

OpenAI's Responses API adds a sharper version of this portability problem.
Reasoning-capable Responses models can return a provider-native item graph:
reasoning items, assistant message items, tool calls, and tool results. When
that graph is replayed with provider item IDs, OpenAI validates that dependent
items are still present. If AssistantMD persists the visible assistant message
ID but drops the paired reasoning item, a later OpenAI turn can fail because the
message item is no longer accompanied by the reasoning item that originally
preceded it.

OpenAI ties useful functionality to that item graph. In tool-heavy Responses
workflows, preserving reasoning items through `previous_response_id` or by
round-tripping the full item graph can let the model continue its prior
reasoning process across function/tool calls. This can improve tool-use quality
and can be token-efficient when server-side response chaining is used. It is
not the same mechanism as prompt caching, which independently caches exact
prompt prefixes for repeated requests. A portable transcript replay can still
benefit from prompt caching when its prefix is stable, but it does not preserve
OpenAI's hidden reasoning continuation state.

## Decision

Provider reasoning and thinking parts are transient by default. AssistantMD may
stream them to the UI during the current turn, but durable chat persistence
removes `ThinkingPart` entries from assistant responses unless the user enables
`persist_model_reasoning_parts`.

When reasoning parts are removed from an assistant response, AssistantMD also
removes provider response item IDs from the remaining assistant response parts.
Those IDs are meaningful only when the provider-native response graph is kept
intact. Keeping the IDs without the reasoning items makes the history look like
an exact Responses API replay while omitting required dependent items.

When `persist_model_reasoning_parts=true`, AssistantMD stores provider-native
reasoning parts with the rest of the assistant response and treats existing
stored sessions containing reasoning parts as intentionally persisted provider
history.

## Rationale

Canonical chat history should preserve the user-visible conversation and the
provider-native parts required to continue it, not every provider-private
intermediate artifact. Making reasoning transient by default keeps replay
history smaller, reduces repeated token spend, and makes it more reliable to
switch an existing session between providers.

Keeping an explicit opt-in setting preserves escape hatches for users who value
durable reasoning artifacts more than portability or token efficiency.

For OpenAI specifically, the default chooses portable transcript history over
OpenAI-native Responses continuation state. This avoids brittle partial replay
of provider item graphs. Users who opt into reasoning persistence also opt into
preserving the provider-specific graph needed by OpenAI reasoning continuation,
with the associated token, portability, and provider-coupling trade-offs.

## Consequences

- Live thinking output can be displayed while a response is streaming without
  becoming part of the default durable transcript.
- New sessions are more portable across providers because persisted history is
  less likely to contain provider-specific reasoning parts.
- Token usage on later turns is lower because prior reasoning artifacts are not
  replayed by default.
- OpenAI Responses history remains valid when reasoning is dropped because
  provider item IDs are dropped with it; AssistantMD replays those turns as
  portable transcript content instead of a partial provider-native item graph.
- Default history may give up OpenAI-specific reasoning continuation benefits
  across tool calls. Prompt caching can still apply to stable repeated prefixes,
  but hidden reasoning state is not preserved unless reasoning persistence or
  another OpenAI-native continuation mechanism is used.
- Users who opt into `persist_model_reasoning_parts` accept higher token use and
  less portable chat history, while preserving the complete provider-native
  reasoning graph for providers that require exact item replay.
- Existing sessions that already contain reasoning parts remain treated as
  intentionally persisted provider history. Existing sessions with reasoning
  removed but lingering provider item IDs are replayed through the portable
  history shape so they do not fail provider validation.

## Evidence

- Current system map: `docs/development/architecture.md`
- Implementation plan: `IMPLEMENTATION_PLAN_REASONING_HISTORY_POLICY.md`
