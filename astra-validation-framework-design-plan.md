# Validation Framework Design and Implementation Plan

## Recommendation and status

Refactor the existing harness into a small outcome-oriented scenario framework with an AssistantMD runtime adapter, reliable execution ownership, and explicit contract admission. Keep Python scenario authoring and the useful existing helpers. Fix trustworthy execution before moving packages or replacing the executor. Governance should prevent missing coverage and make changes to expected behavior reviewable; it should not become a second programming language or claim to prove test quality automatically.

This is an independent Astra design proposal for `dev/test-cleanup`, based on source inspection on 2026-10-26. It is not an implemented architecture, an approved exception policy, or evidence that scenarios pass. No tests, scenarios, live services, or full validation run were executed. The user's stop instruction remains in effect. No product code, harness code, tests, CI, protected documentation, or external library documents were changed for this proposal. The next phase, if this direction is accepted, is Feature Development in small validation-first slices.

The first validation targets for future implementation are reliable collection and cleanup, then three actual application contracts: owner login and CSRF denial, preservation of a settings edit committed after the reset backup, and appending selected file text to an existing draft in the real browser application. These cover ingress, persistence/concurrency, and UI/backend integration without requiring a live model.

## Grounding in the current implementation

| Inspected source | Finding and design consequence |
| --- | --- |
| `validation/core/runner.py` | Discovery imports every candidate and logs/continues on import errors; it takes the first local class exposing `test_scenario`, without actually checking the documented subclass condition. Missing or ambiguous entry points are not rejected consistently. Build a complete inventory before executing scenarios and account for every selected item. |
| `validation/core/runner.py`, `validation/core/base_scenario.py` | `asyncio.run(test_scenario())` finishes before synchronous `teardown_scenario`; the latter saves interactions and does not stop runtime services. Failure cleanup exceptions are suppressed. The runner must own one async lifetime, partial initialization cleanup, and separate primary/cleanup outcomes. |
| `validation/core/system_controller.py` | Construction sets global roots and a secret-key environment variable. Startup resets global runtime state; stop returns early when `is_running` is false and therefore cannot restore every partial-startup mutation. Date control replaces module state. A fresh scenario worker is a practical containment boundary for this application. |
| `validation/run_validation.py`, `validation/core/paths.py` | CLI imports application components after priming roots that default to repository `data/` and `system/`. Collection must not touch persistent runtime state; isolated roots must exist before application imports. |
| `validation/core/base_scenario.py`, `core/logger.py` | Real outcome helpers, soft assertions, timelines, per-event YAML artifacts, boot IDs, and partial event matching are useful assets. The base imports application identity, runtime, logger, and vault behavior, so its name alone does not make it generic. Retain the capabilities while separating ownership. |
| `validation/core/results.py`, `validation/core/reporting.py` | Structured results and requested/expanded selectors already exist. Extend these for inventory completeness, interruption, phase failures, and evidence references rather than replacing all reports. |
| `validation/core/system_controller.py` | The shared app always selects disabled ingress authentication. Authentication policy must become an explicit AssistantMD adapter option so authentic ingress checks use the production policy. |
| `.github/workflows/validation.yml` | CI invokes `integration/core` and the separate npm audit policy check. Installing Chromium does not execute browser scenarios. Inventory must cover permanent checks outside the scenario folder and map them to actual gates. |
| `validation/test_quote_selection_browser.py` | This suite loads real JavaScript modules into authored DOM and replaces fetch plus several collaborators. It is useful existing evidence, but moving it unchanged into `BaseScenario` would not prove real application integration. |
| `validation/test_settings_lifecycle.py` | The missing backup/reset race checks combine real settings state with internal monkeypatches. Preserve the concurrency contract while designing a controlled real-operation interleaving; translating decorators is insufficient. |
| ADR 0010 and ADR 0001 | The accepted design already endorses user artifacts and stable decision events, and acknowledges a process-wide runtime composition root. This proposal strengthens those boundaries rather than inventing a conflicting test philosophy. |

The existing audit records 139 core scenario modules and substantial coverage; this design does not repeat the entire assertion inventory. Its static coverage findings and framework research remain in `test-cleanup-implementation-plan.md`. The external app-agnostic brainstorm, historical user-journey approach, and Logfire note inform the proposal. Their files remain unchanged.

## What a scenario should mean

A scenario is a coherent behavioral experiment against the real application: establish realistic initial state, perform supported actions, await an observable completion boundary, and check outcomes. Its stable identity represents a user outcome or durable subsystem contract, not the production module that happens to implement it. Examples include preserving a newer settings edit, denying unauthorized mutation, and retaining an existing draft while appending a quotation.

Two evidence paths are equally legitimate. A scenario can inspect actual returned or persisted results such as response fields, Markdown files, access outcomes, and durable domain state. It can also inspect a structured validation event for a meaningful internal decision or state that cannot be inferred from those outputs, such as which fallback route was selected. A scenario does not need new instrumentation when ordinary outputs prove its contract. An event assertion should not freeze helper calls, object identities, or implementation order unrelated to behavior.

Prefer existing scenario families when behavior fits their lifecycle and outcome. Use named steps and table-driven cases for meaningful variations. A distinct authorization boundary, concurrency lifecycle, failure-recovery path, or expensive browser dependency can justify a separate scenario. Do not aim for five giant journeys that obscure failures, and do not add a scenario for every function. A bounded persisted-schema or algorithm contract can be legitimate when the schema or algorithmic invariant is itself an approved requirement; it should be classified honestly rather than disguised as a user journey.

Helpers are domain drivers, not alternative implementations. They should invoke real public or supported domain operations and return actual observations. A helper that manufactures the expected result, catches a required failure, or duplicates production logic defeats the purpose. Scenario expected values should come from the behavioral requirement and fixture inputs, not from the same production function being checked.

## Proposed boundaries inside this repository

Start with these internal boundaries; directory names are provisional and package extraction is deferred.

| Boundary | Responsibilities | Must not own |
| --- | --- | --- |
| Generic execution and evidence core | Static inventory, selection, run identity, worker supervision, resource lifetime, phase outcomes, evidence records, event transport/query, reports, small governance checks | AssistantMD imports, settings, vaults, FastAPI, model providers, mandatory browser/Logfire dependencies |
| AssistantMD adapter | Safe bootstrap, real runtime/app construction, authentication settings, isolated stores, deterministic external boundary configuration, logger wiring, readiness, shutdown | Expected scenario answers, generic report policy |
| AssistantMD scenario facade and domain helpers | Vault setup, chat/workflow actions, settings operations, supported state readers, application-specific semantic assertions | Global resource ownership, unregistered process creation, substitutes for app services |
| Optional drivers | HTTP, browser, subprocess communication and their evidence | Product acceptance criteria or mandatory dependencies for applications without those interfaces |
| Repository governance data | Behavioral contracts, scenario registration, required profiles, bounded exceptions and review decisions | A general rule engine or a duplicate catalog of every assertion |

Keep `BaseScenario` as a compatibility facade while its generic responsibilities move behind these boundaries. New helpers should favor composed domain objects over an expanding mixin hierarchy. Existing scenario selectors and the standard command remain usable. It is acceptable for old helpers to contain application-specific internals during migration, provided that debt is visible and new scenarios use the intended surfaces.

The generic layer should use its own ordinary Python logging and evidence writer. Extracting AssistantMD's entire `UnifiedLogger`, Activity logging, settings-aware sinks, and Logfire integration would carry application coupling into the proposed framework. Instead, AssistantMD forwards its explicit validation records to the generic evidence interface. Retain the current YAML sink through an adapter initially; changing storage format is not required to establish the boundary.

### Minimal interfaces

These sketches communicate ownership rather than prescribing an implementation or new DSL:

```python
@dataclass(frozen=True)
class ScenarioSpec:
    id: str
    entrypoint: str
    contracts: tuple[str, ...]
    profile: str
    adapter: str
    timeout_seconds: float

@dataclass(frozen=True)
class RunContext:
    run_id: str
    scenario_id: str
    attempt_id: str
    workspace: Path
    evidence: EvidenceStore
    resources: ResourceScope
    deadline: Deadline

class RuntimeAdapter(Protocol):
    def open(self, context: RunContext) -> AsyncContextManager[RuntimeHandle]: ...

class RuntimeHandle(Protocol):
    async def wait_ready(self) -> None: ...
    async def snapshot_diagnostics(self) -> None: ...

class Scenario(Protocol):
    async def run(self, context: RunContext, runtime: RuntimeHandle) -> None: ...
```

The concrete AssistantMD handle exposes typed application actions, API/browser handles as needed, and paths to scenario-owned state. The generic handle does not need universal chat, API, or vault methods. An adapter factory contains configuration only; construction must not change roots, import path-sensitive application modules, or acquire resources. The context's workspace and evidence paths have one owner: the executor. The adapter must not allocate a competing run directory.

`ResourceScope` can initially be a small wrapper around Python's exit stacks plus explicit task/process ownership. It must register cleanup as each resource is acquired, not wait until startup reports success. Browser/server lifetimes can nest within that scope. Exceptions from setup, action, assertion, cleanup, and evidence finalization remain distinguishable. A context manager is only an interface: startup implementation must itself register rollback before the first fallible mutation.

## Execution, isolation, and evidence

### Collection and process ownership

Use an explicit, import-free entry-point inventory and reconcile it against test-like files and scenario files in the repository. Initially this can be a small checked-in scenario catalog generated from existing paths plus lightweight metadata. Do not execute application imports in the coordinator to discover whether a scenario exists. Workers import their selected scenario only after isolated roots and a scrubbed environment are established. Syntax/metadata errors, missing entry points, duplicate identities, undeclared runnable modules, and import failures become visible failures. Unknown helper modules must be explicitly recognized as support code rather than silently ignored by naming convention alone.

Use a fresh interpreter worker per scenario as the default isolation direction. Do not fork an already initialized application process. This contains AssistantMD's global roots, settings caches, logging configuration, secret-key environment changes, and module clock substitutions without requiring all of them to become safely resettable at once. Keep workers serial first; concurrency is a later optimization after isolation, ports, browser resources, and aggregate resource budgets are proved. A process boundary costs startup time and does not by itself isolate the filesystem or prevent network access.

Allocate a unique workspace before importing the scenario. Set runtime roots explicitly, clear inherited credentials and unapproved application environment configuration, and seed controlled templates into that workspace. Application-specific root setup happens through a minimal bootstrap path before path-sensitive imports. A coordinator should not import `core.logger` merely to report collection errors. Integration workers must not use repository `data/`, repository `system/`, `/app/data`, or `/app/system`; matching path strings alone is insufficient when symlinks are involved.

For deterministic scenarios, declare the external boundaries actually used. Existing configured test-model execution can remain an explicit deterministic dependency, while actual application routing, authoring, tools, and persistence run normally. This does not claim that the real model will make the same decisions. Controlled protocol servers/transports may be appropriate for external dependencies, but the exact substitution policy remains an implementation decision requiring agreement. Replacing AssistantMD services to make outputs match is not the default. Fail offline profiles on unexpected outbound access where the execution environment can enforce it; a metadata flag or a monkeypatch of one HTTP library is not proof of network isolation. Browser and local test-service loopback access must remain possible under any enforcement policy.

### Lifetime and completion

The coordinator persists the selected inventory before starting workers and journals each item through collected, starting, running, cleaning, and terminal states. Every selected required item must acquire a terminal result or an explicit not-run/interrupted result. No aggregate success is inferred merely from the subset that finished. Preserve the requested selector, expanded identities, source revision and dirty-diff identity, policy version, and environment/dependency identity needed to interpret a result.

Within a worker, construct the scenario, enter the adapter, execute actions/assertions, capture diagnostics, and close resources in one async lifetime. Apply a scenario deadline with a separate cleanup grace period. Track spawned tasks and processes, request cooperative cancellation, then terminate owned process groups if needed. A worker crash or hard kill can prevent graceful cleanup; the supervisor must report that truth, reap what it owns, and retain already-written evidence. It must not claim teardown succeeded when it could not observe completion.

Primary assertion failures remain primary even if cleanup or evidence flushing also fails. A scenario whose assertions pass but whose cleanup fails is non-passing infrastructure execution, not a pass. Interruptions preserve completed results and identify interrupted/unstarted scenarios. Successful restarts within a scenario retain scenario identity while advancing runtime boot identity. Never use automatic retries to silently turn a failing required attempt into green; an explicit diagnostic rerun retains both attempts.

### Evidence contract

Each scenario result references observations, failed checks, and lifecycle diagnostics. Preserve raw received artifacts where safe and record normalized views separately with the normalizer's identity. Use field-level or semantic checks for volatile data; use exact comparison only where exact content is the contract. A normalizer that removes access decisions, revisions, or source provenance is a behavioral change and requires review. A hash helps identify evidence but does not prove the evidence or its expected answer was correct.

Events need a small transport envelope: run/scenario/attempt correlation, event name, schema version, runtime boot or producer identity, producer-local sequence, and payload. Do not infer distributed causal order from timestamps or a single directory listing. Queries should scope to the action's correlation and cursor. Assert absence only after a defined completion/quiescence boundary; a missing event while work is pending proves little. The current count-based cursor and sorted YAML filenames are adequate compatibility inputs but should not become the generic concurrent event protocol.

App event contracts define the minimum behavioral keys and the point at which the observation is true. An event describing a successful commit must be emitted after the commit, not before attempting it. An internal routing decision may be valid evidence without duplicating all final-output assertions; when both are relevant, check both. Events are not automatically authoritative merely because the app emitted them, so critical admission probes must confirm they detect the actual wrong decision or outcome.

Browser failures should retain a trace or screenshot, browser console errors, relevant requests, and matching server/runtime evidence. The browser loads the actual served application and backend. Required UI behavior should use stable accessible interactions or deliberate test identifiers, not CSS layout details. Fixtures may establish files/configuration before startup; replacing fetch with authored success responses is not the real-app browser profile.

Evidence collection needs explicit redaction and retention because API exchanges, browser traces, settings snapshots, and model interactions can contain secrets or user content. Deterministic fixtures use synthetic credentials. Live experiments require a deliberate evidence policy. Retain evidence failure as a visible run condition rather than discarding it behind a passing assertion result.

## Governance that is small enough to maintain

### A contract catalog, not an assertion catalog

Start with three contract records for the pilot and an explicit legacy classification for the existing inventory. A contract record needs a stable ID, a short behavioral requirement, why it matters, the observable proof surface, and a review reference establishing the expected behavior. Existing scenario registrations refer to those IDs; the contract-to-scenario matrix is generated from registrations rather than manually maintained twice. Add contracts when a new durable requirement needs a reviewable identity, not for every implementation detail, table row, or assertion.

For example, `settings.reset.preserves_newer_edit` says a reset may apply only to the revision captured by its backup; a conflicting newer edit survives, with a visible conflict outcome. `chat.fallback.selects_available_model` names a routing decision with a structured event as its proof surface when the final reply alone cannot identify the chosen model. The catalog is useful because deleting or relabeling the last admitted scenario for either requirement becomes visible. It is not useful if every source edit requires another manifest entry.

Legacy classification must not be presented as completed semantic review. Maintain a finite reconciliation ledger for root tests and only grandfather the exact existing files/content; new or changed candidates must follow admission. Required contracts found only in root tests remain coverage debt until real equivalent scenarios run through the standard gate. Do not make the inventory green by silently calling them experiments. The project should not claim complete governance until every retained permanent test has an execution path and unique required coverage has been migrated or explicitly resolved.

### Hard checks and review judgments

| Mechanically enforce | Requires semantic review |
| --- | --- |
| Every recognized permanent test entry point is registered as a product scenario, framework/infrastructure check, opt-in experiment, or narrowly recorded transition item | Whether a file omitted by naming heuristics actually contains a hidden test; whether fixtures and actions represent the intended use |
| Required contracts reference selected admitted scenarios; selected items and terminal results reconcile exactly | Whether those scenarios actually establish the contract rather than asserting a tautology |
| Collection/import/setup/timeout/cleanup/evidence failures produce non-success | Whether an event reports the meaningful decision or merely restates expected text |
| Existing required contracts cannot disappear from the baseline without an explicit reviewed disposition | Whether removal, replacement, or a weaker comparison is justified |
| Metadata schemas, expiration dates, broken references, missing entry points, unexpected file additions, and forbidden import patterns where unambiguous | Whether a private import, fixture write, failure injection, or comparator crosses the intended boundary |
| A report records the actual command, revision, selection, policy, and outcomes | Whether that evidence is sufficient to claim the feature complete |

Use ordinary structured data and a small checker, not a custom policy language. AST/import-pattern checks can flag patching AssistantMD internals, source-string assertions, swallowed assertion errors, and new dynamic test entry points. Treat uncertain cases as review findings, not universal semantic proof. Python is too expressive for a static checker to establish that a scenario exercises the real system or detects the intended bug.

The same local validation command should perform structural admission checks and selection accounting before execution, and CI should consume its machine-readable result. Preserve `python validation/run_validation.py run integration/core` as the deterministic product gate. Framework self-checks and infrastructure checks can have explicit companion subcommands/profile roles under the same entry point without pretending to be user journeys. Security audit commands may remain dedicated CI jobs with declared ownership; one coherent inventory does not require wrapping every command in `BaseScenario`.

Local focused execution is allowed and must label itself as partial coverage. It cannot produce a full-gate attestation. CI must verify the required inventory and run completion against the same source and policy identity. Editing the checker, inventory exclusions, gate command, contract requirements, baseline, or comparator is a governance-sensitive diff. A checker controlled by the same change cannot provide independent approval for itself: compare against the trusted base revision and require maintainer review for policy/contract changes. A single-user project can use the user's review as this authority; elaborate organizational roles are unnecessary.

### Admission for agent-authored changes

1. Identify the existing contract and scenario to extend, or propose a new behavioral contract with the user-visible reason it deserves permanent coverage. Establish the expected outcome from the request, accepted contract, or defect evidence before generating an oracle from current code.
2. Show the smallest real application action sequence and its evidence. For a quotation scenario, assert the resulting composer content preserves the existing draft and contains the selected file quotation, using the real browser application. For a fallback-routing contract, assert the action-correlated structured decision event names the actual admitted fallback and reason; the reply alone need not identify that choice. Neither example requires tracing internal helper calls.
3. Declare special dependency control or interleaving needs. Prefer real external boundary controls and supported fixtures. If the reset race cannot be reproduced reliably through current surfaces, propose a narrow synchronization mechanism and its policy explicitly; do not silently replace the backup implementation.
4. Demonstrate the check rejects a plausible wrong outcome. A regression should fail on the unfixed behavior for the intended reason. For a new contract, use a narrow temporary fault in the actual application or a reviewed negative case. A missing import/private method is not the desired red result. Select fault demonstrations by risk; do not require an expensive mutation campaign for every small assertion.
5. Demonstrate reliable execution and evidence under the normal entry point, inspect overlap with existing scenarios, and review whether a harmless internal refactor would force edits. Run only the appropriate focused checks during development. Such future execution is not authorized by this design task.
6. Admit the scenario through review of the requirement, action, assertion, and failure evidence. Another agent can provide critique; it cannot create independent truth merely by agreeing. Record a concise rationale for new scenarios, test deletion, assertion weakening, baseline changes, comparator/normalizer changes, and exceptions.

A narrow exception record should name scope, reason, compensating evidence, review owner, and expiry or removal condition. Changing profile, skipping required work, suppressing failures, or approving all received outputs must not silently bypass a contract. An explicit maintainer waiver may permit a merge while retaining a non-passing/deferred validation status; it must not rewrite the result as a pass. Unreviewed self-asserted `approved: true` metadata is not approval.

## Keep three activities distinct

| Activity | Question | Result and authority |
| --- | --- | --- |
| Deterministic application validation | Does the real application preserve the contract under controlled dependencies and known inputs? | Repeatable outcome checks and the ordinary merge gate |
| Live model/agent evaluation | How often and how well does an actual model or external agent perform across representative cases? | Opt-in datasets, model/configuration versions, costs, repeated trials, rubric/scorer versions, failure samples, and uncertainty; not arbitrary exact prose snapshots |
| Coding-agent process compliance | Did the coding agent use approved commands, inspect required context, and avoid bypassing test policy? | Repository/tool audit evidence and review; does not prove product behavior |

Live evaluation may reuse the artifact and runtime interfaces but should not inherit a claim of determinism. It needs selected connection grants and explicit telemetry setup in isolated state. The Logfire investigation shows why simply forwarding an API key is insufficient for normal encrypted connections and instrumentation. Do not mount the developer's live data/system roots to make experiments work. The application adapter owns any authorized connection provisioning and telemetry bootstrap; generic execution must not require Logfire. Evaluation score thresholds, credential handling, and stochastic retry policy are separate future decisions.

## Execution component decision

Keep the current CLI, result model, and direct Python scenario execution as the starting point. The immediate problems are not solved by moving scenarios into pytest or by retaining `BaseScenario` around implementation-coupled assertions. The existing research establishes that pytest custom collection is an available execution option; it does not establish that it reduces this project's total machinery. Evaluate it only after the lifecycle contract is explicit and only if a thin adapter removes more maintained code than it adds. Do not build an executor-plug-in architecture merely to keep that option open.

Playwright's direct Python browser APIs are a concrete component to reuse for the browser pilot; the prior research already covers this capability. Reuse standard async/resource/process libraries where suitable, and retain the existing structured reporting. Artifact approval tooling can later supply diffs for selected stable outputs. TextTest, Robot, and Behave need no additional trial unless a pilot exposes a specific gap they solve with less driver and maintenance code. There is no current requirement for a new authoring language or wholesale baseline-comparison workflow.

The largest cost is likely converting implementation-coupled checks and consolidating setup, not writing a runner loop. Process isolation adds startup overhead; real browser scenarios add runtime and diagnostics cost; semantic contract review takes attention. Stable event schemas themselves become maintenance obligations. Benefits should be measured by missing-test detection, defect detection, authoring effort, and refactor resilience rather than scenario count or framework branding.

## Incremental implementation sequence

### Slice 1: Make the inventory and results trustworthy

Inventory all current scenario files, root Python/Node/browser tests, fixture helpers, and infrastructure checks without executing application imports. Preserve current selectors, fail collection visibly, reject ambiguous entry points, and report the full selected-versus-completed set. Record legacy items and root-only contract debt accurately. Add focused framework checks using tiny synthetic scenario fixtures that intentionally fail collection, assertion, cleanup, and result writing; these are tests of harness behavior, not mocks of AssistantMD.

Do not delete root tests in this slice. They are evidence for the migration ledger even where their current style is unsuitable. Structural enforcement begins with preventing new unaccounted additions; the final gate must also resolve retained legacy omissions. Avoid an exemption glob that lets future tests bypass admission indefinitely.

### Slice 2: Establish safe execution ownership

Move run-path allocation and async teardown under the runner. Make adapter construction inert and partial startup cleanup unconditional. Introduce a worker boundary with isolated roots before imports, explicit environment policy, deadlines, cancellation reporting, and incremental evidence. Keep execution serial. Adapt the existing facade so the same scenarios can execute without a bulk rewrite. Preserve `try/finally` cleanup in existing scenarios initially; make repeated adapter close harmless and remove duplicated ownership later where verified.

Use tiny real fixture applications/adapters that write an output, emit an event, fail during startup, hang, crash, or start a child process. Their deliberately controlled behavior tests the framework's subject under test; it does not replace AssistantMD services in an application scenario. Prove constructor failure, startup failure, assertion failure, timeout, interrupt, and cleanup failure yield complete non-success accounting and preserve the first error. Verify that child processes are reaped and that subsequent workers have independent roots, settings, secrets, and clocks. Keep these checks in the declared framework self-check profile under the standard entry point, not another undiscovered pytest suite. A fresh process addresses contamination, while runner-owned shutdown addresses resource correctness; both matter.

### Slice 3: Prove the application adapter with three contracts

Implement real owner-authentication and CSRF actions under explicit policy; preserve the API outcome and safe evidence. Extend settings lifecycle coverage to a backup-fenced concurrent edit using real stores and actual operations, with an agreed interleaving mechanism. Add the real-server browser quotation/draft scenario and capture useful failure artifacts. Seed only synthetic data/credentials. These pilots should use the same command, reports, resource ownership, and admission path.

The settings pilot has a real open design issue: the API does not necessarily expose a pause between backup and reset. Options include an approved synchronization hook at a stable boundary or a focused supported domain-operation sequence for the revision invariant plus a separate API error projection check. Do not promise deterministic API concurrency through sleeps, and do not smuggle a replacement backup function into a helper. The pilot must identify exactly which behavior is proved and which remains uncovered.

### Slice 4: Apply minimal governance and prove maintenance value

Add the three contract records and scenario metadata, generate their coverage matrix, and implement trusted-base diff checks for removed requirements, new exclusions, and changed proof surfaces. Pilot the admission checklist on one agent-authored scenario amendment. Show a wrong-output failure, a wrong-decision-event failure where applicable, and a harmless internal refactor that requires no scenario/assertion edit. Measure pilot setup code, scenario/helper lines, runtime, diagnostics usefulness, and necessary review effort. Expand the catalog only when the pilot demonstrates value.

### Slice 5: Reconcile and migrate existing coverage

Work through the root-test assertion ledger by risk: ingress authentication, reset/migration races, browser workflows, then remaining MCP/advanced-shell and storage/import boundaries. Extend existing scenarios where coherent; create a new one only for a distinct outcome/lifecycle. Keep old tests until the equivalent unique required evidence is demonstrated under the standard gate. Remove duplicates in small reviewed changes. Replace prompt/editorial source-string checks with behavior where they are not explicit text protocols. Keep framework self-tests clearly separate from product contracts and make their execution explicit.

No full-suite execution is part of this proposal or authorized by it. Future targeted and full validation must respect the user's current stop instruction; a later explicit authorization is needed to resume the full run. Once authorized and the implementation is stable, run the complete deterministic profile as required by the repository cadence and record its actual revision and results. Do not claim merge readiness from this static plan.

### Slice 6: Consider general-purpose extraction only after local success

Prove the generic core can run a tiny second executable application with real file output and a decision event without importing or installing AssistantMD. Verify optional HTTP/browser/logging dependencies remain optional. Only then decide whether a standalone package reduces maintenance. Publishing a package, designing a broad plug-in ecosystem, replacing the executor, and adding distributed parallel scheduling are out of the initial scope.

## Acceptance criteria for the implementation

1. A deliberately broken import, missing registration, duplicate entry point, or removed last required scenario cannot produce a passing standard run; the report names the affected item or contract.
2. Every selected scenario is accounted for after success, failure, timeout, interrupt, or worker crash. Primary, cleanup, and evidence failures remain independently visible. Zero selected or zero completed required scenarios cannot count as a successful gate.
3. Collection and execution do not read or mutate persistent application roots. Temporary-root isolation and owned-resource cleanup are demonstrated under partial startup and failure, including environment/clock contamination between workers. Process isolation is not mislabeled as an OS security sandbox.
4. The owner-authentication pilot detects a real authorization/CSRF defect, the settings pilot detects loss of a newer edit, and the browser pilot detects loss of the existing draft or an incorrect quotation through the real served application.
5. An internal-decision contract can be admitted using a correlated, structured validation event without requiring a private-call assertion. A scenario already proved by output does not need gratuitous instrumentation.
6. A representative harmless implementation refactor leaves the pilot scenario intent and assertions unchanged; any required helper/adapter change is explained and measured. Merely hiding unchanged brittle assertions behind a helper fails this criterion.
7. An agent-authored addition has a reviewed requirement and an intended negative demonstration, executes through the standard command, and adds protection or replaces brittle/duplicate protection without losing unique coverage.
8. Existing required root-only contracts are migrated or explicitly reported unresolved; no blanket experimental relabeling, automatic baseline acceptance, or silent required skip makes the gate green.
9. A meaningful failure report leads to the action, expected versus observed outcome, relevant evidence, runtime identity, and exact rerun selection without inspecting incidental private implementation state.
10. The generic boundary runs a second tiny app independently before extraction is claimed. Local AssistantMD value must already be demonstrated even if extraction is never pursued.

## Decisions to resolve before their implementation slices

- Confirm the allowable external dependency controls and how they are declared; the existing deterministic model provider is evidence of a current mechanism, not blanket approval for mocking application internals.
- Choose the settings race interleaving mechanism and whether it justifies a stable synchronization boundary. Treat unsupported deterministic concurrency as an honest coverage limitation until resolved.
- Choose the smallest contract/catalog file format after the three examples exist; avoid premature schemas for every possible kind of application or assertion.
- Decide worker launch/readiness and real-server ownership from the pilot, including how the ASGI path relates to production startup and shutdown. Avoid duplicate runtimes merely to supply a browser URL.
- Set practical deadlines, evidence retention/redaction, and CI browser cost limits based on pilot evidence rather than guessed global thresholds.
- Define the trusted approval mechanism for contract and governance changes in this single-user repository. Local review references can be simple, but branch-authored metadata alone cannot supply independent authorization.
- Resolve the existing conflict between required root planning artifacts and CI's root Markdown allowlist before merge. Keep this plan at the root for the requested planning checkpoint; do not silently change CI or move it into protected documentation.

## Final deferred documentation review

After implementation behavior and scope are established, review likely updates to `docs/agent-guides/testing-and-validation.md`, applicable development/cleanup guides, validation architecture documentation identified by the implementation, and a proposed ADR for the execution/contract boundary if warranted. Reconcile relevant current guidance without rewriting historical decisions as though they had always described the new system. Discuss the affected files and present the exact proposed text or diff to the user; obtain explicit approval before creating, editing, deleting, or moving anything under `docs/`, `README.md`, or `RELEASE_NOTES.md`. Apply only that approved draft. Documentation work and the plan's final merge disposition remain pending until this review.
