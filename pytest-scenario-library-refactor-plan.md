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

## End-of-migration cleanup backlog

Keep the canonical actionable gap list at validation/registry/pytest-gaps.json. Per-source gap_ids in pytest-migration.json link ports to that backlog. Add every newly discovered non-equivalence with its missing proof, retained original coverage, evidence and proposed resolution; avoid blocking unrelated migration work. Close a gap only with real replacement evidence or an explicit reviewed disposition. Keep product-contract questions distinct from migration parity and runner capability decisions.

The initial backlog covers settings API conflict translation, legacy-secrets abrupt-death boundaries, the unresolved root-alias evaluation contract and timeout/descendant cleanup guarantees required for cutover. Subsequent batches append their gaps there. Existing originals remain authoritative while those gaps are open.

## Cleanup and migration batches: 2026-10-28

Removed the superseded validation/experiments pytest prototype after its generic reporter/observation responsibilities moved into scenario-harness and its app cases/fault profile passed against the installed wheel. Archived its source and comparison metadata under validation/runs/skill_evaluations/retired_pytest_comparison_source, and moved its two standalone ledger entries to retired_files with replacement/evidence pointers. The active standalone inventory is now 45 files / 248 named tests; deferred browser/backend sources remain intact.

Archived completed skill-evaluation and earlier harness-cleanup plans with ignored evidence and removed disposable temporary app snapshots. Keep this file as the active root implementation plan. Historical run artifacts and the known root-path discrepancy remain preserved. Protected documentation is unchanged.

Reorganized test_pilot.py into four domain-named staged test modules with shared workflow/context input templates under support/workflows.py. The four existing behavior functions are unchanged and passed after the move (4 passed, 98.30 seconds, validation/runs/pytest_migration/domain_profile_split/report.json). The old omnibus source is preserved as evaluation evidence and in Git history.

Three Sol agents are porting independent subsystem batches while the parent owns cleanup, admission metadata and cross-batch checks. The first five original scenarios all passed their isolated baseline (run 20261028_152922_783295): unified file tools, directory cleanup, vault file-reference API, settings database migration and settings lifecycle. Their staged ports have focused passing evidence; mappings, limitations and logical commits are recorded as batches finish. The next five originals also passed their selected baseline (run 20261028_154045_955516): content search, vault manifest, startup/rescan, ingress authentication and legacy secrets migration.

First port batch: five source modules now map to nine passing pytest cases. File-tool and directory expectations were preserved, file-reference API coverage includes additional real symlink-denial checks, and settings atomic-save failure uses actual SQLite constraint input. Settings database migration remains partial because the old mocked API 409 translation has no replacement proof. Initial image-path and subprocess-logger fixture failures remain preserved alongside affected-case reruns. All three agents completed focused Ruff/Black/MyPy checks. See migration_batches and per-source migration records in validation/registry/pytest-migration.json.

Second port batch: five more source modules now map to thirteen passing cases across vault content search/state refresh, ingress authentication and legacy secrets migration. Critical ingress assertions and source identities remain intact; the new evidence does not grant manual contract approval. Legacy secrets migration is explicitly partial: real SQLite/filesystem failure fixtures prove durable exception/retry behavior but do not replace original abrupt-death probes at three private injection boundaries. Existing crash sources and baseline observations remain required until reviewed replacement or retirement.

Current staged profile: 26 deterministic cases, comprising four reorganized pilot cases and 22 new passing cases from ten source modules. Fourteen original core identities have staged node mappings; 132 still have no staged replacement, and two mapped sources retain explicit gaps. This is accounting rather than a behavioral coverage percentage. Twenty-two project policy checks and production Ruff/Black/MyPy (293 API/core sources) passed; each agent also passed focused lint/format/type checks. Staged-environment MyPy uses a separate cache per agent/environment after shared caches produced checker-internal module-resolution failures. These failures did not justify changing assertions or suppressing types.

Generated artifact source is excluded from ordinary pytest discovery by validation/conftest.py. An isolated collection test verifies a saved run-source file is never imported while real source remains discoverable. Final admission/source-hash checks are complete; default staged collection selects exactly 26 deterministic cases and excludes the fault profile. Machine-readable evidence for every passing node and inactive-runtime lifecycle is indexed at validation/runs/pytest_migration/migration-batches-20261028.json.

Original sources/catalog stay authoritative until reviewed cutover. Installed old/new environments remain separate, the committed dependency pin and CI execution remain unchanged, and the full-suite stop remains in force. A staged port is not automatic critical-contract acceptance. Known non-equivalence must remain visible, including the mocked settings API conflict translation and any old private-injection crash assertions without replacement proof.

## Current inventory and evidence

Inventory checked on 2026-10-28 at AssistantMD commit fe60474:

| Category | Current accounting | Refactor treatment |
|---|---:|---|
| Deterministic integration/core scenarios | 146 | Map each protected behavior to pytest coverage or an explicitly reviewed disposition |
| Declared experiment scenarios | 15 | Preserve opt-in execution and service/cost requirements |
| Standalone opt-in experiment scripts | 4 | Preserve explicit ownership and admission; assess separately from core |
| Deferred legacy browser/UI files | 27 | Preserve and account; browser refactor remains deferred |
| Deferred legacy backend files | 18 | Inventory behavior, resolve known gaps and identify duplicated/private-interface checks |
| Retired pytest comparison/fault prototype | 2 files / 8 functions archived | Installed package/staged profiles own active execution; historical evidence retained |
| Active standalone sources in the current ledger | 45 files / 248 names | Structural accounting, not execution or coverage completeness |

There are 161 registered scenario declarations in total, not 161 deterministic core scenarios. Four pilot contracts currently have review/evidence bindings. The current inventory is structurally complete but does not establish complete behavioral coverage or maintainer acceptance of those pilots.

The four-case pytest comparison passed using the real application and production tool/API paths. The refined heartbeat case observed real staleness at 60.8 seconds and cancelled/rolled back through production operations. The crash profile captured a genuine startup DatabaseError, continued in the same worker, then survived a native abort by replacing the worker and running another real app. See validation/runs/skill_evaluations/retired_pytest_comparison_source/pytest_comparison/comparison.json and the comparison reports under validation/runs/pytest_comparison. This is a successful pilot, not proof that every scenario or execution guarantee has migrated.

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

## Migration continuation: 2026-10-28

Continue three parallel Sol batches after recording gaps for end cleanup: file listing/search/concurrency (four original sources), workflow cancellation/governor/lifecycle (four), and system startup/migrations/template refresh (three). The parent owns baseline selections, shared admission metadata, the gap backlog, cross-batch checks and logical commits. Keep the installed environments separate and the full-suite stop in force.

Batch 3 completed: eleven originals passed selected baseline run 20261028_161227_753784 and thirteen staged cases passed across file_boundaries_batch3, workflow_runtime_20261028_retry and startup_20261028. Real configuration replaces search-cap patching; real workflow execution replaces governor/scheduler stubs. Queue serial/concurrent/global-limit behavior, cancellation rollback, timeout metadata/history, restarts, startup migrations and explicit template refresh retain real output/event proof. File serialization and active move/edit contention remain explicit partial ports. All authors passed focused lint/format/type checks, using isolated caches. The workflow collection-before-bootstrap import failure and its correction are retained as fixture evidence.

Batch 4 completed: eleven originals passed selected baseline 20261028_162011_211070; nineteen new recovery, authoring, encrypted/locked startup and database migration cases have passing focused evidence. Actual execution exceptions/public task status replace fabricated failure wrappers. Real constraints/filesystem inputs replace migration-phase fault mocks. Original controlled input conditions are preserved or explicitly scoped; the locked import warmup matches original prewarmed setup, and cold-import side effects remain separately recorded. Task snapshot reactivation, activity restore serialization, encrypted guarded-write serialization and default instruction semantics remain explicit partial ports. All original sources and historical evidence remain intact.

After these batches, 36 of 146 original core identities have staged mappings and 110 remain unmapped. The staged default profile contains 58 expanded cases with actual passing pytest verdicts across focused runs; this is not a whole-suite or evidence-correctness certificate. The user requested a migration pause after this batch for a red-flag review. No further ports will start until that review is discussed.

## Paused for post-batch red-flag review

Migration is paused at the user's explicit request after the active batch. Three independent Sol reviews support the simplified pytest/xdist direction and found no returning scenario DSL or general pattern of manufactured outcomes in the inspected ports. Review is limited to consequential correctness/isolation issues, not polishing. Full assessment: validation/runs/pytest_migration/architecture_review/assessment.md.

Three blockers are recorded in pytest-gaps.json: observation-redaction-equality (confirmed wrong secret expectation passes), capture-mutates-application-logging (test projection changes real app sinks and warning identity, invalidating registered-secret absence proof), and failed-teardown-worker-reuse (global context removal can admit another app without confirming prior services stopped; no active leak reproduced). These require evidence-boundary and failed-cleanup policy corrections, not a return to the old custom runner. No fixes were applied as part of this review.

Ingress and settings lifecycle source records now explicitly block their logging-confidentiality proof pending correction; other direct API/database assertions are not automatically invalidated. Passing case counts are actual pytest observations only. Keep migration paused for discussion of these findings before adding more ports. All originals remain retained and the full-suite stop remains in force.

## Evidence-boundary corrections and focused verification: 2026-10-28

The three review blockers are resolved with negative controls and installed-wheel evidence. Generic package commit 19c7dad preserves deep-copied original observation values for matching; serialization and diagnostics redact separate copies. All 27 package tests pass from source and from a separately installed wheel. The staging environment alone now uses that wheel (SHA256 2212893a13dc9627c54ca193d620c0a540a6f1aea98d561b24320a8ed7563871); the normal AssistantMD environment, dependency pin and CI engine remain unchanged.

Application capture now projects only exported YAML copies and passes independent original records to the observer. Production activity/Logfire emission and warning deduplication see original values. Two real-app logging cases verify distinct warnings, raw activity values, exported redaction and mutating callback isolation. The real-app wrong-secret and deliberately unsafe-log negative controls both fail as required (exit 1); their exported reports/observations redact the registered values. These controls are explicitly admitted opt-in experiments, excluded from the default core selection.

Failed cleanup now permanently poisons the controller and stops the current pytest invocation through pytest's session failure mechanism after retaining the actual phase report. Real filesystem obstruction of the activity log makes runtime shutdown raise IsADirectoryError. Both direct teardown failure and a caught earlier close failure terminate with exit 2, record failed cleanup despite a cleared runtime sentinel, and leave the following selected case incomplete with no phases. A failed controller restart is rejected. This is a conservative invocation abort, not an automatic replacement or a promise that every prior service completed shutdown. Ordinary application exceptions with successful cleanup still permit same-worker recovery; the existing native-abort probe still recovers using an xdist replacement worker (expected two failures and two passes, exit 1).

Five focused cases pass together in one reused worker: logging capture (two), settings lifecycle, owner-session authentication and failed-workflow vault rollback. Existing migrated behavioral assertions were not edited. Their original confidentiality oracles now observe actual production values. This corrects those evidence boundaries without certifying all earlier focused runs. The staged core now contains 60 expanded cases; source migration remains 36 of 146. No further ports started and the full-suite stop remains in force.

Evidence is indexed in validation/runs/pytest_migration/architecture_review/fix-verification.json, with reports under evidence_controls_fixed_20261028, cleanup_failure_fixed_20261028, caught_cleanup_failure_fixed_20261028, trusted_evidence_rerun_20261028 and crash_recovery_fixed_20261028. Repository Ruff, Black (590 files), MyPy (293 production files) and 22 CI/collection policy checks pass. The canonical gap backlog retains the historical findings and marks these three blockers resolved; eleven other gaps remain open, including timeout/descendant guarantees. Maintainer acceptance, broader migration and protected documentation remain pending.
