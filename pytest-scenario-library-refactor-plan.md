# AssistantMD behavioral scenario library refactor plan

## Status and relationship to the package plan

Implementation underway. Refactor AssistantMD's application validation to pytest using the generic plugin and skills described in scenario-harness/pytest-plugin-implementation-plan.md. The package plan owns generic Python code, plugin self-tests, reference applications and skills. This plan owns AssistantMD application fixtures, behavioral scenarios, coverage decisions, canonical structure, project governance and CI.

The existing runner and its a066ef6 dependency pin stay operational during staged development. Replacing the generic repository's engine does not authorize deleting application coverage or changing expected behavior. Do not rewrite the Git history needed to install the old pin. Staging does not authorize a full-suite run, remote push or merge; the application cutover follows the gates below.

## Implementation checkpoint: 2026-10-28

- validation/registry/pytest-migration.json records source identities for all 146 core scenarios and 18 pending backend files, plus experiment/browser accounting. Most entries are explicitly static candidates pending semantic review; this is not a completed behavior/parity audit or coverage attestation.
- validation/pytest_scenarios contains a real application fixture and four staged business cases using the installed generic plugin. All four passed in a separate migration environment; each emitted lifecycle evidence with runtime_active_after_close=false. A final pilot against the independently built wheel passed all four in 90.83 seconds. Wheel SHA256 is f72a01d22e1b2e289bc0bba7a2363efd7ee00f4bb84d596c83ea82f83300eb26 at package revision 4b9daa3; installed_wheel_verification in the inventory records package versions and reports. The normal environment and committed dependency pin still use the old engine. See commit a26c5d5 and the evidence references in the inventory.
- The explicitly selected staged fault profile produced exit 1 with statuses failed, passed, failed, passed. It preserved sqlite3.DatabaseError, fatal-signal diagnostics and unconfirmed crash cleanup. The startup fault, same-worker recovery and abort shared one PID; final recovery ran in a replacement PID. The final installed wheel repeated those four outcomes in 23.08 seconds; original SQLite exception, retained fatal stack and distinct replacement PID were checked programmatically. These are expected fault-profile results, not passing core coverage.
- The staged pytest admission manifest accounts for exact test/support paths, source hashes and declared functions, without importing candidates or hiding the staging directory from legacy accounting. Actual collection enforcement is project-owned and unprivileged; default collection selects exactly four business cases from both repository and staging invocation directories, while faults require explicit file/node selection. The original 161 declarations and 47 legacy files remain intact.
- Catalog validation and trusted Git audit code now belong to AssistantMD. The approval workflow no longer installs the harness; the audit runs under isolated Python with site packages disabled. Twenty-one focused repository admission/collection policy checks passed, and the existing inventory, governance and CI-governance scenarios passed as a three-case focused selection (run 20261028_021958_750266).

Next steps are semantic review and subsystem migration batches, fixture lifecycle edge cases, timeout/descendant-cleanup decisions, critical-contract evidence reconciliation and the eventual atomic dependency/collection/CI cutover. Browser work, protected documentation updates, full-suite execution and maintainer acceptance remain pending. No original scenario, deferred source or historical run artifact has been removed.

## Current inventory and evidence

Inventory checked on 2026-10-28 at AssistantMD commit fe60474:

| Category | Current accounting | Refactor treatment |
|---|---:|---|
| Deterministic integration/core scenarios | 146 | Map each protected behavior to pytest coverage or an explicitly reviewed disposition |
| Declared experiment scenarios | 15 | Preserve opt-in execution and service/cost requirements |
| Standalone opt-in experiment scripts | 4 | Preserve explicit ownership and admission; assess separately from core |
| Deferred legacy browser/UI files | 27 | Preserve and account; browser refactor remains deferred |
| Deferred legacy backend files | 18 | Inventory behavior, resolve known gaps and identify duplicated/private-interface checks |
| Pytest comparison/fault experiment files | 2 files / 8 functions | Use as migration evidence; fault cases intentionally fail |
| All standalone sources in the current ledger | 47 files / 256 names | Structural accounting, not execution or coverage completeness |

There are 161 registered scenario declarations in total, not 161 deterministic core scenarios. Four pilot contracts currently have review/evidence bindings. The current inventory is structurally complete but does not establish complete behavioral coverage or maintainer acceptance of those pilots.

The four-case pytest comparison passed using the real application and production tool/API paths. The refined heartbeat case observed real staleness at 60.8 seconds and cancelled/rolled back through production operations. The crash profile captured a genuine startup DatabaseError, continued in the same worker, then survived a native abort by replacing the worker and running another real app. See validation/experiments/pytest_comparison/comparison.json and the comparison reports under validation/runs/pytest_comparison. This is a successful pilot, not proof that every scenario or execution guarantee has migrated.

## Target testing model

- Every AssistantMD behavioral scenario starts a real isolated application instance. Invoke the relevant production boundary: API, tool or supported user-operation entry point.
- Observe actual files, API/tool results, persisted state and semantic validation-sink events. Use stable event names, selected data fields, checkpoints and correlation IDs.
- Fixtures may seed controlled input, including deliberately corrupt persisted state needed to test recovery. They must not manufacture the application result or emit the event that the scenario is supposed to observe.
- Avoid private runtime-object assertions, collaborator call counts, mocked outputs, patched strategies and clock manipulation that merely make expected results appear. Production input/configuration and supported application controls are legitimate integration inputs.
- Keep ordinary pytest fixtures and Python assertions. Do not rebuild BaseScenario's broad inheritance API under a different name or wrap old test_scenario methods and call that a port.
- Application setup always needs isolated data/system roots, credentials policy, service ownership and sink capture. A new application object alone does not prove process-global caches, logger handlers and background work were reset.
- Initially use one sequential xdist worker with bounded replacement. Increase parallelism only after application isolation is verified. Explicit serial debugging must identify its reduced crash protection.

## Ownership and canonical structure

Keep generic reporting, event queries, artifact ownership and xdist integration in the package. Keep AssistantMD configuration, startup/shutdown, authority, vault/workflow actions, credential-field recognition and sink binding here. Keep repository reviewer identities, trusted Git/CI authorization and contract policy here.

The proposed final behavioral roots retain the existing taxonomy:

- validation/scenarios/integration/core for deterministic application behavior.
- validation/scenarios/experiments for explicitly selected live, slow-service or diagnostic behavior.
- validation/support for application fixtures and small domain-specific integration helpers.
- validation/fixtures for static input files/workspaces.
- validation/registry for project-owned critical-contract policy, source/evidence provenance and unresolved coverage records.
- validation/runs for ignored run outputs.
- .github for repository-specific CI infrastructure and authorization.

Use normal pytest file/function naming in the migrated library. Preserve logical behavioral identity separately from filenames and pytest node IDs where policy or evidence needs a stable identity. Splitting a large scenario into independent cases is acceptable; case counts do not prove parity.

Do not mix new pytest functions into the old runner's scenario tree during staging: its static discovery requires BaseScenario subclasses. Stage new cases under validation/pytest_scenarios/integration/core and validation/pytest_scenarios/experiments until the final collection/configuration cutover. Treat those as explicitly admitted temporary roots. Remove the staging tree after the reviewed cutover; do not leave two permanent libraries.

## Phase 1: Inventory contracts and establish the migration map

Create a project-owned migration map before bulk conversion. For every current core scenario, record its source identity, behavior/purpose, production action boundary, real dependencies, asserted outputs/events, fixture inputs, private-interface/mocking concerns, required contract links, planned pytest cases and disposition.

Include the 18 legacy backend files in the same analysis. Distinguish an already-protected outcome, a real uncovered requirement, an obsolete implementation-specific test, a framework self-test and an unresolved case. Preserve unknowns as gaps; registration or a passing unrelated scenario is not replacement evidence.

Identify cases that use fake agents/models, monkeypatched strategy functions or direct private state. Determine what behavior they actually prove. Prefer a real workflow or application input that produces the failure naturally. A test that does not exercise a real model/provider must not be presented as model/provider behavior coverage. Keep genuine live dependencies opt-in and record what deterministic CI does not cover.

For pure framework tests, move ownership to the plugin repository. Repository-specific CI policy tests belong with their project policy/infrastructure; they are not AssistantMD application behavior coverage and need not start an irrelevant application. Preserve their actual protocol/fixture scope honestly.

Exit: all 146 core sources and 18 pending backend files have traceable proposed dispositions; the 15 declared experiments, four standalone scripts and deferred browser files remain accounted. This map is a behavioral coverage aid, not a percentage derived from file or assertion counts.

## Phase 2: Prepare collection, inventory and governance for staging

- Introduce runner-aware project inventory for explicitly admitted pytest staging files while retaining old scenario and legacy accounting. Do not add a blanket directory exclusion. Unregistered test files must remain detectable.
- During staging, keep the old core source/catalog intact. New pytest cases are comparison/shadow coverage until their behavior mapping is reviewed. Do not count both versions as additional behavioral coverage.
- Keep collection explicitly bounded. Verify actual collected pytest nodes match the intended roots/profiles and reject unexpected tests elsewhere, including accidental legacy or fault-profile collection.
- Run pytest collection of candidate code only in ordinary unprivileged CI: collection imports tests and conftest files. Privileged approval jobs inspect Git data and accepted-base policy without executing candidate Python or installing candidate-selected dependencies.
- Preserve manual CI authorization of test/oracle/policy changes. Canonical structure, required fixtures/metadata and unexpected collection can be checked mechanically; semantic test quality still needs review and skills.
- Decide the minimal permanent registry after migration. Avoid maintaining a second copy of pytest discovery solely for its own sake. Retain the data needed for critical contracts, unresolved coverage and project admission policy; changing current admission policy requires explicit review.

Exit: new pytest work can coexist safely with the old library without confusing discovery, hiding files or weakening the trusted review boundary.

## Phase 3: Adopt the generic plugin and real-app fixture

Wait for the companion package plan's installed-release pilot. Consume an immutable, tested revision; use an editable sibling checkout only for coordinated local work. Normal CI installs the committed dependency. Do not let a local checkout silently determine accepted CI behavior.

Use a separate disposable migration environment while staging the new plugin: the old engine and new plugin have the same distribution name and cannot both occupy one environment. Keep the normal application environment and committed old pin intact until cutover. Provision the staging environment from the app dependency baseline, install its recorded new-plugin revision or explicit editable override, and invoke that environment's Python directly so ordinary uv synchronization does not restore the old pin. Record the installed plugin identity in evidence. The final dependency/lockfile switch occurs atomically with CI/collection cutover, not at the first pilot.

Refactor the experimental app fixture into supported application integration. It must own real runtime creation, temporary roots, settings/model caches, secrets, logging capture, API clients, background tasks and shutdown, including partial startup and teardown failures. Reuse useful SystemController/VaultManager behavior where appropriate; keep helpers focused and avoid carrying the complete BaseScenario facade forward.

Bind the actual AssistantMD observation sink to the plugin. Keep event-field interpretation in app expectations and generic subset matching in the package. Inject artifact/secret-recording facilities into application helpers instead of importing the old global scenario-harness context.

Validate the existing four real-app cases with the installed plugin, without importing the in-tree experimental reporter, old BaseScenario or old runner lifecycle. Retain real output and sink assertions, including the scheduled positive control and production timer used for the slow heartbeat case.

Run the application exception/worker-abort profile explicitly and verify expected exit 1, both recorded faults, both recovery cases, original exception stack, fatal diagnostics and unconfirmed dead-worker cleanup. Ordinary core CI must never discover the intentionally failing fault profile accidentally.

Exit: the installed generic plugin supports the app fixture and pilot cases; startup, same-worker cleanup and replacement-worker evidence have concrete verification.

## Phase 4: Port and review in subsystem batches

Batch by real behavior boundaries such as file/vault operations, settings/credentials/identity, workflow/task lifecycle, chat/session/context behavior, ingestion/search and external integration boundaries. Determine batch sizes from complexity; do not mechanically convert all sources at once.

For each batch:

1. Select the original behaviors and establish their current focused baseline using isolated roots. Identify failures or environment requirements before changing test shape.
2. Write pytest cases that exercise production entry points and real observations. Preserve meaningful conditions; record every changed, split or retired expectation and its rationale.
3. Remove assertions on private formatters, runtime object layout, method calls and incidental text. Replace them with production outputs/events where that protects the same behavior; record any remaining gap instead of silently dropping it.
4. Reproduce positive and relevant failure paths without mocking application outcomes. Use explicit fixture data and genuine application failures. Negative oracle probes must be labelled and must not be presented as production mutation testing.
5. Run only the original/new focused selections and review their evidence. Case-count or assertion-count equality is insufficient for approval.
6. Record real-app startup/cleanup and the output/event contract in the migration map. Commit the validated batch as a logical unit. Keep its original source until cutover/replacement approval.

Tests with timing or external-service costs need a declared profile and budget. Preserve required automatic coverage when separating slow cases; do not simply move a difficult contract out of CI. The real heartbeat-staleness case currently needs about a minute. Any supported virtual-time approach would need its own explicit policy/proof; do not patch private clocks or thresholds for convenience.

Exit: each current core behavior has reviewed pytest coverage or an explicitly accepted retirement/reclassification, and known legacy gaps have a clear status.

## Phase 5: Reconcile evidence and required contracts

- Preserve ingress authentication/CSRF and settings revision-fence semantics through real application behavior and persisted output.
- Review the existing custom-runner lifecycle contract by required outcome. Keep relevant failure/cleanup/interruption/crash evidence guarantees; retire obsolete request/result protocol details only with explicit rationale and replacement proof.
- Keep project manual-approval guarantees and the accepted-base trust boundary. The generic plugin does not own GitHub authority or approve changes to tests.
- Keep historical baseline/fault observations and source identities. Add current proof for the new pytest oracles and record meaningful source/contract changes. Do not bulk-rebaseline hashes, regenerate approvals or relabel old faults as observations of new code.
- Represent pytest's actual exit codes and phase outcomes in the new evidence adapter. Do not force them into old runner semantics or assume the old JSON schema is still authoritative.
- Describe known coverage as covered, partial, missing, deferred or retired with evidence/rationale. Optional code coverage can inform investigation; it cannot establish behavioral completeness.

Exit: required contracts, source identities, current observations and pending maintainer acceptance are accurate under the new execution model.

## Phase 6: Cut over CI and remove old infrastructure

Perform a reviewed, coherent cutover after the staging library, plugin release and contract requirements are ready:

1. Move the admitted pytest library into the final canonical roots, update root pytest configuration and CI selections, and verify collection from the actual invocation directory. Preserve deterministic versus live/diagnostic profiles.
2. Switch the dependency and standard commands to pytest plus the installed plugin. Affected surfaces include pyproject.toml/uv.lock, scripts/dev, the normal validation workflow, trusted governance dependency installation and registry/inventory tools.
3. Before removing old package APIs, relocate the registry/Git-review mechanisms that AssistantMD still needs into app-owned support/CI. Current consumers include validation/support/inventory.py and validation/support/ci/audit.py. Prefer a standard-library-only trusted checker so privileged review jobs need not install pytest or the plugin. Remove the old trusted installer if it has no remaining purpose; any retained dependency must come only from the accepted base pin and have its actual prerequisites installed. Validate the trusted path before changing or activating required CI statuses.
4. Remove unused BaseScenario, old adapter/execution/runner/result/reporting facades and validation/project.toml only after all imports/callers are accounted. Preserve useful app-specific controllers, fixtures, event contracts and domain helpers in their new owners.
5. Remove duplicated experimental reporter/observation code after the package owns it. Retain or rehome useful app-specific integration probes under an explicit profile; generic plugin fault tests live in the package repository.
6. Resolve the 18 deferred backend files from the migration map. Delete only explicitly superseded/obsolete cases after replacement proof. Keep unresolved behaviors visible. Leave the 27 deferred browser/UI files intact.
7. Preserve ignored historical run artifacts unless the user authorizes their deletion. Do not touch persistent data/system roots.

Exit: one application behavioral library and one execution path, installed generic support, accurate project inventory and no hidden dependency on the old engine or temporary sibling-path injection.

## Verification and merge gates

- Every admitted behavioral case uses the real app fixture and an appropriate production action boundary. Review this semantically; fixture inheritance or a marker alone cannot prove it.
- Verify actual collection, plugin packaging/installation, output/event assertions, serial debugging policy and xdist crash evidence.
- Exercise repeated/reordered application lifecycles and startup/teardown errors in reused workers. Verify isolated state and background/cache/logging cleanup. Parallel application cases require their own evidence before enabling multiple workers by default.
- Verify required timeout/descendant-cleanup guarantees identified in the companion plan before dropping the corresponding old protection. Document and explicitly accept any changed guarantee.
- Preserve CI/manual review scope and meaningful test-failure discrimination; unexpected collection, missing cases or protected-oracle changes must not silently pass.
- Run focused checks and batches during development. The user's current stop on full-suite runs remains in force; obtain an explicit lift before a final full deterministic pre-merge run. Once authorized, run the final profile after stabilization and preserve its artifacts.
- Complete repository static checks and a final source/import/CI inventory. Do not push or merge without authorization.

## Use the behavior-first skills in this application

Adopt the generic planning skill after it is available. A feature plan should identify the observable contract and production entry point, existing coverage, real fixture/dependency requirements, expected meaningful red result, implementation work, green evidence and review needs. Extend existing scenarios when that is the appropriate coverage boundary; avoid a new file for every implementation detail.

The development skill should demonstrate red/green through the real app, keep the behavioral oracle stable during implementation refactors and inspect emitted state/output. The review skill should catch private-interface assertions, simulated results, weakened expectations, unsupported coverage claims and accidental profile changes. Diagnosis should distinguish app failure, fixture failure and worker loss using the recorded evidence.

Project guides must describe AssistantMD-specific fixtures, paths, authority, event contracts, profiles and governance. Avoid copying the generic package manual into local instructions.

## Browser work and other boundaries

Browser/UI refactoring is deferred. The generic plugin can retain arbitrary artifact attachments, but first-class browser lifecycle support, Playwright integration and app-owned journeys need a separate capability decision. Do not count deferred source files as executed browser coverage or add them to the core profile during this refactor.

This plan does not authorize changing application behavior to make a port pass, weakening an expectation without review, publishing package releases, altering branch protection, accepting pilot contracts or modifying production state.

## Completion criteria

- All 146 current core scenario behaviors have traceable, reviewed outcomes in the migration map; splits/retirements are explicit and test counts are not used as completeness proof.
- All experiments and deferred legacy sources have accurate ownership/profile/disposition records, including unresolved gaps and deferred browser work.
- The real-app pytest library runs through the installed generic plugin, with small project-owned fixtures and inspection-ready artifacts.
- Old execution infrastructure and duplicated experiment support are removed after their responsibilities have an owner.
- Canonical collection and trusted governance remain enforceable, and evidence/source identities remain meaningful.
- Required runtime guarantees and the authorized final deterministic profile have passed before merge preparation is complete.

## Documentation review before applying updates

After the implementation contract stabilizes, prepare exact drafts/diffs for the affected local files, starting with AGENTS.md and docs/agent-guides/testing-and-validation.md. Also inspect feature-development.md, planning.md, project-structure.md, relevant architecture/ADR text, README.md and RELEASE_NOTES.md for changes actually needed. Discuss the affected files and proposed text with the user and obtain explicit approval before creating, editing, moving or deleting protected documentation.

The new local testing guide should focus on AssistantMD integration and link to the generic package's approved installation/usage and skills. Product documentation must describe the current contract. Planning documents intentionally live at the repository root during this effort; before merge, agree their final disposition and any protected destination, rather than weakening the existing root-Markdown CI rule. Apply only approved documentation changes, validate the final references/commands and finish the merge-readiness review.
