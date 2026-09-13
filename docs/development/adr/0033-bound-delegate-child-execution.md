# 0033 - Bound Delegate Child Execution And Preserve Failure Handoffs

## Status

Accepted.

## Context

A delegate child can perform many model and tool cycles before returning to its
parent. A runaway child may repeatedly request a failing operation, exhaust an
unexpected amount of model usage, or spend its complete tool budget without
producing a useful synthesis. Blindly retrying that delegation can duplicate
cost and tool effects.

Delegate history is not canonical chat history. Persisting provider-native
child checkpoints or automatically replaying child tools would introduce a
second continuation system without durable work-unit identity, effect claims,
retention, or restart reconciliation. At the same time, discarding all child
progress on a bounded failure deprives the parent of evidence and encourages a
broad retry.

## Decision

Run each delegate as an isolated child agent with four independently
configurable guardrails:

- a model-request limit;
- a tool-call limit;
- a cooperative wall-clock timeout; and
- a consecutive identical structured-failure limit for child tool calls.

Each guard may be disabled independently. Configuration guidance recommends
leaving at least one of the request, tool-call, or timeout limits enabled, but
the runtime does not couple those settings or reject an all-disabled
configuration. The repeated-failure guard fingerprints canonical tool name and
arguments, counts only structured failed outcomes, and resets after a success,
raised execution error, or changed call. It does not block repeated successful
reads, polling, or concurrent calls already admitted.

Give every child a system-owned, compact flight card before caller-supplied
instructions. The stable policy lives with other system prompts, while runtime
composition adds the effective tool-call budget. The child is told to stay in
scope, avoid repeating a failed call unchanged, stop tool use before exhausting
its disclosed budget, and return a compact handoff. Model-request and timeout
ceilings remain operator controls rather than child planning inputs.

Classify child tool results through the shared structured terminal-state
classifier used by chat tool events and delegate auditing. Once a delegate
starts, emit exactly one completed, failed, or cancelled terminal lifecycle
event. Parent cancellation remains authoritative: log bounded progress and
re-raise cancellation. A timeout cancels and awaits cooperative child work
before returning its structured failure.

On a bounded or classified failure, return the latest bounded partial output,
shared usage, an audit distinguishing settled and unsettled child calls, and
available cache or artifact references. Do not automatically replay child
tools, manufacture a tool-free completion, persist provider-native child
history, or resume a child after process restart. The parent uses the handoff
to narrow any continuation and must inspect durable state before repeating a
possibly unsettled mutation.

## Consequences

- Delegate cost and runaway behavior have several independent containment
  boundaries without forcing one deployment policy.
- Children can plan against their effective tool-call budget without exposing
  controls that are not actionable during a model turn.
- Structured failures are visible consistently in child audits, parent chat
  tool state, and repeated-failure protection.
- Limit, timeout, initialization, and runtime failures retain enough bounded
  evidence for a narrower parent continuation.
- Cooperative cancellation cannot provide a hard wall against blocking or
  cancellation-suppressing third-party code.
- Delegate progress remains process-local and non-resumable; durable delegate
  work units require a separate decision with identity, claim, effect, and
  retention contracts.

## Evidence

- Current system map: `docs/development/architecture.md`; tool contract:
  `docs/tools/delegate.md`
- Implementation: `core/tools/delegate.py`, `core/constants.py`,
  `core/tools/failures.py`,
  `core/llm/capabilities/delegate_repeated_failure_guard.py`
- Validation: `validation/scenarios/integration/core/delegate_tool.py`
