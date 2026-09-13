# 0041 - Bind Google OAuth State And Mutations To Credential Generations

## Status

Accepted.

## Context

A Google OAuth client ID identifies the credential relationship under which a
grant was created. Treating it like ordinary display metadata can leave tokens
or pending authorization attempts usable after the client identity changes.
Deleting stale records best-effort is not sufficient because cleanup can fail,
and changing a client ID from `A` to `B` and back to `A` can otherwise revive
state from the first generation.

## Decision

Give every Google connection a positive monotonic OAuth generation. Increment
it exactly once when the normalized client ID changes; display name, default
selection, and capability-preference changes preserve it.

Store a random credential identity with each client secret and replace that
identity whenever the secret is replaced. Bind token grants and pending PKCE
state to both the connection's OAuth generation and the current credential
identity. Status resolution, OAuth start and completion, refresh, and
capability availability reject mismatched bindings before using or exchanging
the state.

A client-ID change invalidates the previous client secret, pending attempt,
token grant, account identity, and capability readiness. Replacing only the
client secret invalidates pending attempts and grants while preserving the
client ID generation. Cleanup removes stale encrypted records when possible,
but binding verification is the authoritative invalidation mechanism.

An authenticated unbound legacy client secret may be assigned to generation 1
on first use. Unbound legacy tokens and pending attempts require
reauthorization; silently binding them could preserve grants created under a
different client identity.

Keep generation and credential identities internal. APIs and activity logs
expose sanitized readiness transitions, not binding values or stale account
data.

Capture the exact generation, credential identity, and source grant before an
external token exchange, account lookup, or refresh. Persist the resulting
state only if those guards are still authoritative after the external wait. A
late response cannot attach itself to the currently configured credential or
overwrite a newer completed authorization merely because the connection ID is
unchanged.

Disconnect preserves configured client credentials while atomically rotating
their internal credential identity and deleting pending and granted state.
Connection deletion records a permanent sanitized deletion entry in
the metadata tables before purging the connection's encrypted
namespace. Reconcile incomplete purges after restart and never reuse a deleted
connection identity. The deletion entry, generation, and credential bindings
are authoritative; physical stale-record cleanup is defense in depth.

## Consequences

- Changing security-sensitive credential identity differs intentionally from
  editing connection presentation or preferences.
- Returning from client ID `B` to `A` cannot revive grants from an earlier `A`
  generation.
- OAuth persistence and readiness checks must carry and verify both bindings.
- Completion and refresh responses that race with credential replacement,
  disconnect, reauthorization, or deletion fail closed.
- Disconnect can invalidate active grants without deleting reusable OAuth client
  configuration.
- Connection deletion commits metadata and encrypted state together in `access.db` after
  interruption without permitting the deleted identity to become usable again.
- Conservative legacy handling may require users to reconnect rather than
  preserving an unverifiable grant.
- Physical stale-record cleanup remains useful for hygiene but is not part of
  the authorization proof.

## Evidence

- Implementation plan: `BRANCH_HARDENING_IMPLEMENTATION_PLAN.md`, Stages 3 and
  7–10
- Current implementation boundaries: `core/integrations/google/connection.py`
  and `core/integrations/google/oauth.py`
- Related decisions: ADR 0028, ADR 0034, ADR 0037, and ADR 0038
