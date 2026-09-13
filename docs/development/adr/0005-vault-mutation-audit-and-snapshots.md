# 0005 - Route Vault Mutations Through Audit And Snapshot Infrastructure

## Status

Accepted, backfilled. Amended by
[0025 - Generalize Vault Mutations Into Durable Activities And Revisions](0025-durable-vault-activities-and-revisions.md).

## Context

Vault files are the user's durable workspace. AssistantMD can mutate those files
from chat tools, workflows, ingestion, and code execution. Users need activity
visibility and a way to recover from failed, cancelled, or timed-out tasks
without making Git a runtime dependency.

## Decision

Maintain vault state as a rebuildable manifest plus retained task mutation
audit and snapshot records. Route supported vault writes through
`core.vault_state.file_mutations` so the system can capture before-state
snapshots, record mutation provenance, refresh the manifest, and roll back task
mutations when configured.

ADR 0025 generalizes task mutation ownership into source-neutral durable
activities, extends retained snapshots into exact-path revision history, and
defines the shared atomic restoration contract.

## Rationale

The filesystem remains the source of truth, so manifest data can be rebuilt from
vault files. Mutation audit and snapshots cannot be reconstructed after the
fact, so they are captured at write time. Keeping this layer neutral lets
ingestion, memory, retrieval, and UI activity views consume vault facts without
coupling vault state to one feature.

## Consequences

- New vault-mutating code should use the shared mutation API.
- Snapshot files live under managed system storage, not inside the vault.
- Rollback covers AssistantMD-routed mutations with retained snapshots.
- Audit rows and snapshots are retention-bound runtime safety artifacts, not a
  full version-control system.
- Vault identity is stable through `AssistantMD/vault.yaml`, while vault name
  remains display and compatibility metadata.

## Evidence

- Current system map: `docs/development/architecture.md`
- Recovered sources: PR #42 `vault_state.md`,
  `ingestion_vault_activity_plan.md`, PR #43 `memory-implementation-plan.md`
