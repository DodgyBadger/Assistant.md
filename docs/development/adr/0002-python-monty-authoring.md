# 0002 - Use Python Monty Authoring For Workflows And Context

## Status

Accepted, backfilled.

## Context

AssistantMD needs user-authored automation for scheduled workflows and chat
context assembly. The authoring surface must remain markdown-first, inspectable,
and safe enough for a single-user local system while still allowing real
orchestration logic, tool calls, cache access, and model calls.

## Decision

Use one markdown authoring format for workflows and context templates: YAML
frontmatter plus one fenced Python block executed in a Monty sandbox. Host-owned
capability functions form the side-effect boundary. Authoring files are loaded
through `core/authoring`, and `run_type` distinguishes workflow and context
execution.

## Rationale

Real Python made authored orchestration clearer than custom step syntax for
branching, reuse, and composition. Monty keeps execution in a sandbox, while
host capabilities keep file access, tool calls, cache operations, history
retrieval, and final outputs explicit and auditable. Keeping workflows and
context templates on the same substrate reduces drift between two automation
systems.

## Consequences

- Capability helpers are the integration boundary between sandbox code and host
  runtime.
- Frontmatter and loader contracts matter because they scope execution shape.
- Context assembly and workflow execution can share helper registration,
  sandbox execution, and direct tool binding.
- Documentation for authoring should describe current Python helper contracts
  rather than separate template languages.

## Evidence

- Current system map: `docs/development/architecture.md`,
  `docs/use/authoring.md`
- Recovered sources: PR #40 `authoring_architecture_plan.md`,
  `dsl_removal_refactor_plan.md`, `workflow_python_sdk_plan.md`,
  `workflow_python_sdk_sketch.md`
