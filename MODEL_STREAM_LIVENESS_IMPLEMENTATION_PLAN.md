# Model Stream Liveness Implementation Plan

## Objective

Prevent a provider stream that remains connected without producing usable model events from leaving a chat indefinitely in `running`, while preserving long agent runs that continue to make model or tool progress.

## Testable slices

1. Add a validated `model_stream_idle_timeout_seconds` setting and a typed idle-timeout failure classification. A deterministic stalled stream must fail within the configured idle window and expose an actionable, retryable error.
2. Apply the idle deadline between primary model events, reset it after each event, suspend it while an explicitly observed tool call is running, and publish bounded execution-task progress for model and tool phases.
3. Make unresolved custom-provider base-URL secret pointers fail during configuration/model construction rather than reaching HTTPX as a malformed literal URL.
4. Disable the OpenAI SDK's internal retries wherever AssistantMD supplies its own bounded retry transport, preventing multiplicative retry attempts for OpenAI, OAuth, and OpenAI-compatible providers.
5. Update current-contract documentation and release notes, run the affected integration scenarios directly, then run Ruff, Black, and MyPy. The maintainer retains ownership of the full validation profile and live-provider comparison.

## Event contracts

- `model_stream_idle_timed_out`: emitted when no usable model event arrives inside the configured idle window; includes task, session, model, attempt, timeout, and active-tool count.
- Existing `chat_retry_scheduled` and terminal chat failure events retain ownership of retry and final failure reporting.

## Invariants

- Long chats are not capped by total elapsed time while model events or tool transitions continue.
- A running tool is observable and is not mistaken for a silent model stream.
- Automatic replay remains subject to existing side-effect recovery policy.
- No retry layer can silently multiply AssistantMD's configured HTTP retry budget.
