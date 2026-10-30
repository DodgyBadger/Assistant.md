# Remaining validation gaps

17 review items remain; the canonical browser UI suite is complete and verified. Gmail connection UI and content-import UI items have been resolved and removed. Completed items have been removed; item 3 is tracked in the external To Do list. Items 64–74 were identified by the former-root backend reconciliation on 2026-10-30 and have not yet been reviewed by the user. Numbers below are stable review IDs. The implemented practical alternatives and their explicit evidence limits are recorded in `validation/registry/pytest-gaps.json`.

- **37. Gmail account-specific token routing:** Verify that explicitly selecting each Gmail account sends that account’s token. Current transport fixtures use the saved default grant; selected-account metadata alone is insufficient.

    **Recommendation:** **Keep, high priority.** Exercise the actual selected-account path with two different grants and capture the outgoing authorization for each selection. Avoid a factory that substitutes the default grant; change composition only if investigation shows the real path cannot be exercised or is incorrect.
    - Defer to the end - I want to address this cooperatively

- **44. Successful advanced-shell MCP execution:** Verify actual SSH launch configuration, root forwarding and client closure. Configuration gates and refused-connection behavior pass; successful SSH execution lacks local test infrastructure.

    **Recommendation:** **Defer to explicit infrastructure work.** Establish a reusable local SSH/container fixture, then verify actual launch arguments, roots and connection closure. Keep current admission/refusal checks in core; do not count an unreachable SSH endpoint as successful execution coverage.
    - Defer, I'm not convinced we're going to be able to adequately test this through scenarios.

- **48. MCP OAuth authorization lifecycle:** Test actual discovery and authorization start, live callback rejection and recovery, completion timeout, superseding attempts, live disconnect and failed supersede recovery. Current tests begin with saved pending records.

    **Recommendation:** **Keep.** Add a real local authorization-discovery/MCP server and test start through callback completion, supersede, disconnect, expiry and durable-failure retry. Keep the saved-pending tests as restart coverage, and assert operation identity and cancellation reasons through actual activity records.
    - Defer - I want to understand better

- **54. Browser resource admission:** Verify actual successful-session concurrency limits and low-memory refusal when browser work resumes. Call budgets are covered without launching Chromium.

    **Recommendation:** **Defer with browser infrastructure work.** Use a finite-memory container and held local pages to test real concurrency, memory refusal before launch and slot release after cancellation. Retire the fake browse counter once the actual resource guarantees are observable.
    - Approved

    **Current status:** Deferred by the approved recommendation. UI browser sessions now run, but the application BrowserTool concurrency/memory admission contracts are separate backend behavior and remain unverified. The UI pilot does not resolve this item.

- **62. Old adapter versus current evidence capture:** Review the old requirement to rewrite the application’s original log with redacted values. Current capture preserves original observations and redacts exported evidence; the old adapter self-test still fails that expectation.

    **Recommendation:** **Retire the requirement that the harness rewrite producer logs.** Preserve original observations and redact exported evidence. Separately verify that production code does not emit credentials in the first place, so harness redaction cannot hide an application logging defect.
    - Defer - I want to understand better

- **63. Additional isolation policies:** Decide whether fresh-process, environment-whitelist and `HOME`/temporary-directory isolation are warranted beyond the implemented safeguards. Core now has Python egress restrictions, explicit subprocess admission, controller-enforced deadlines and inherited-session cleanup. Complete native/child egress containment, credential-file isolation and deliberately detached process ownership remain outside the current guarantees.

    **Recommendation:** **Retain the current bounded safeguards and review stronger isolation only against a concrete contamination or containment requirement.** Keep isolated data/system roots, explicit test credentials, Python outbound restrictions, subprocess admission, cleanup quarantine and controller deadlines. Review `HOME`/temporary-directory replacement and stronger OS containment together when discussing this item; do not treat existing guards as an OS sandbox.
    - Defer - I want to understand better

## Former validation-root backend review

The 18 retained backend files contain 93 reviewed contract groups: 38 are covered by cited canonical assertions, two obsolete/self-test groups are retired, and 53 remain partial or open. These are contract-group counts, not scenario or code-coverage percentages. The detailed mapping and evidence limits are in `validation/registry/backend-migration.json`. Existing deferred SSH/OAuth decisions remain items 44 and 48; the newly identified groups below need explicit follow-up. No retained backend file is yet accepted for wholesale deletion.

- **64. Authentication boundary parity:** Explicit startup mode/secret-file validation; full peer/header/WebSocket and malformed-cookie matrix; domain-separated session keys; failure-window expiry, successful-login reset and bounded peer eviction. Real HTTP admission covers part of this, not every primitive contract.

    **Recommendation:** Keep the security outcomes. Exercise startup with isolated configuration and actual HTTP/WebSocket admission; use an explicit clock input or a bounded short real window for throttling. Review codec argument-only checks separately from application security guarantees.

    **Status:** Newly identified; not yet reviewed.

- **65. Chat preparation and MCP snapshot cleanup:** Deferred resume semantic instruction ordering and releasing acquired MCP resources when primary/deferred preparation fails. Existing context composition and manager closure scenarios do not establish those exact paths.

    **Recommendation:** Keep lifecycle/resource guarantees. Use the real chat/deferred-review path with a held local MCP server and an actual invalid preparation input; observe remote session closure and application events. Retire literal guidance-copy and fake registration counters.

    **Status:** Newly identified; not yet reviewed.

- **66. Typed decision execution:** Successful TypeSafe question/state mapping, typed output and usage, confidence/probability normalization, timeout classification and configured classifier/alias/secret construction. Current cases cover configuration and an actual rejection, not successful classification.

    **Recommendation:** Keep. Drive the actual decision classifier and configured factory against a controlled external TypeSafe HTTP boundary; assert typed results and real semantic log events. External transport substitution is acceptable; do not replace the classifier, logger, model resolver or secret service.

    **Status:** Newly identified; not yet reviewed.

- **67. Import output compensation:** OCR-specific allocated image namespace; partial output-write cleanup; preserving artifacts modified after the import wrote them; job-finalization failure clearing output/manifest state. Existing source-cleanup failures are a different stage.

    **Recommendation:** Keep the durable file/job outcomes. Use a controlled external OCR response and real filesystem/database failure boundaries with explicit interleavings. Preserve user-modified files and report incomplete compensation; avoid monkeypatching the application storage/job service.

    **Status:** Newly identified; not yet reviewed.

- **68. Remaining alias and catalog boundaries:** Older model naming/profile forms, shipped adaptive-thinking/schema capabilities, repeated catalog generations and same-model alternate promotion, full malformed/shared-alternate matrix, simultaneous cold loads with one complete upgrade/backup. Current shipped-catalog adoption/collision/idempotence already has coverage.

    **Recommendation:** Keep identity, atomicity and backup guarantees. Use native legacy configurations and concurrent fresh processes with a real packaged-catalog input. Decide whether speculative future catalog fixtures justify a dedicated supported fixture; do not infer them from the current catalog.

    **Status:** Newly identified; not yet reviewed.

- **69. MCP acquisition, capacity and cleanup:** Cancellation during partial multi-connection acquisition, stdio capacity/serialization and HTTP progress, endpoint/credential capture races, cleanup failure telemetry, and synchronous invalidation failure/worker-call branches. Real shared sessions and cold-start fences cover only part of this.

    **Recommendation:** Keep resource/isolation behavior. Prefer held real local MCP transports and captured remote sessions/events. Test externally observable progress and release; review private synchronous error-injection cases for a supported failure boundary or explicit retirement.

    **Status:** Newly identified; not yet reviewed.

- **70. Remaining settings storage races:** Exact original YAML bytes when the source changes between read and backup; interrupted first-import transaction recovery; inherited packaged-default changes versus explicit equal overrides; global-reset API/service backup-edit race. Ordinary transaction rollback and backed-up revision domain checks already pass.

    **Recommendation:** Keep persistence/provenance guarantees. Use real source files/SQLite, a bounded execution barrier where necessary, and actual public reset requests. Keep backup bytes and revision outcomes as the oracles; do not mock repository save or reset services.

    **Status:** Newly identified; not yet reviewed.

- **71. Provider error variants and secrecy:** Anthropic/Google SDK rejection variants, unstructured bodies, explicit message bounds/redaction matrix and wrapped/cyclic errors. Existing real OpenAI-compatible failures and OAuth rejection protect only part of the original contract.

    **Recommendation:** Keep supported-provider diagnostics and non-disclosure. Feed controlled external HTTP/SDK boundary failures through real application paths. Review arbitrary cyclic helper inputs as a separate public error-normalization contract; avoid constructing fake application failures just to exercise branches.

    **Status:** Newly identified; not yet reviewed.

- **72. Search executable and timeout failures:** Distinct stable missing-executable, process-start OS-error and timeout results. Actual rg search, query/scope validation and structured results are already covered.

    **Recommendation:** Keep stable errors. Run isolated configuration/process environments that omit the executable, select an unusable external executable, or run a bounded held external process. Leave the application search service real and verify cleanup after timeout.

    **Status:** Newly identified; not yet reviewed.

- **73. Shipped defaults and initial model setup:** The enumerated numeric/strategy/security/provider defaults and the unset-default-model storage/compaction setup contract. Existing editor/override scenarios are not a complete assertion of those defaults.

    **Recommendation:** Keep agreed product defaults and setup readiness. Assert actual freshly bootstrapped metadata/settings and public compaction admission. Separate deliberately fixed product defaults from incidental catalog versions before pinning additional exact values.

    **Status:** Newly identified; not yet reviewed.

- **74. Advanced-shell deployment/configuration:** Complete profile/mode startup matrix, key/trust bootstrap artifacts, full stdio envelope budget and status privacy. A restricted or refused SSH connection does not exercise configuration/bootstrap combinations.

    **Recommendation:** Keep deployment boundaries separately from successful SSH item 44. Use isolated bootstrap subprocesses and generated test keys, without a live SSH server where unnecessary; place deployment-only checks with CI infrastructure and application runtime boundaries in core.

    **Status:** Newly identified; not yet reviewed.
