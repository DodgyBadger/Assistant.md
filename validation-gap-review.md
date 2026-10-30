# Deferred validation gaps

Nine validation review items are explicitly deferred by the user following completion of the OAuth acknowledgment case. They remain unverified; this is not a coverage-complete claim. Numbers are stable review IDs. Product item 3 (vault-root deletion) remains separately tracked in the external To Do list; its existing decision/priority is unchanged. Current evidence and deferred scope are recorded in `validation/registry/backend-migration.json` and `validation/registry/pytest-gaps.json`.

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

All 93 contract groups across the 18 retained backend sources are reconciled: 79 covered, two retired and 12 partial. These are contract-group counts, not scenario or code-coverage percentages. The backend closure pass verified 39 distinct targeted cases; the subsequent SSH implementation verified 17 SSH cases and four existing MCP regression cases, all with completed cleanup; the full core suite remains stopped. Authentication, typed decisions, search failures, shipped defaults, local shell configuration/bootstrap and app-side SSH/MCP transport items are complete and removed. Original backend sources remain retained; per-file acceptance is recorded in the registry. The account/OAuth priority follow-up verified 25 distinct targeted/regression cases with completed cleanup; selected-account routing and the pre-DNS credential-binding race are complete. OAuth item 48 is now closed by three real failure/restart scenarios, verified alongside eleven existing live OAuth cases. The remaining nine review items are deferred.

- **65. Chat preparation and instruction composition:** Semantic instruction inclusion/order for advanced-shell availability and deferred resumption still needs a requirements-backed application-output oracle. Exact flight-card copy and fake registration counters are not being frozen. Primary/deferred preparation cleanup needs an agreed real failure input: the original tests injected a shell-resolution exception, whereas actual SSH failures normally produce sanitized unavailability.

    **Recommendation:** Clarify the instruction/preparation contracts and an honest post-acquisition failure input. Do not substitute app shell services or create synthetic agent failures solely to reproduce the old mocks.

    **Status:** Deferred at the user’s request; completed local portions remain recorded in the registry.

- **67. Import post-publication failure:** Only the narrow post-publication binary-write fault remains: a binary artifact is partially published before the writer raises. Prior Markdown cleanup, user-edit preservation, OCR allocated namespaces and job-finalization compensation now pass.

    **Recommendation:** Agree an honest filesystem/process fault input for the post-write window before adding this case; do not replace application storage methods.

    **Status:** Deferred at the user’s request; completed local portions remain recorded in the registry.

- **68. Future managed-catalog generations:** Hypothetical successive managed-catalog generations, alternate promotion/removal across those generations and a supported packaged-catalog fixture remain undecided. Historical family names, shipped SDK profiles, public custom rename/promotion, shared-alias failures and simultaneous cold loads now pass.

    **Recommendation:** Discuss the value and supported input contract for future packaged-catalog generations; public custom renames are not equivalent proof.

    **Status:** Deferred at the user’s request; completed local portions remain recorded in the registry.

- **69. Remaining MCP race and error boundaries:** Cleanup-failure telemetry and worker-thread propagation of invalidation failure remain unverified. Real stopped-manager synchronous invalidation failure is now proved through the OAuth API; the same public shutdown input is available for future worker-thread hardening. Ordinary worker-thread invalidation, credential binding, stdio capacity/cancellation and HTTP progress already pass.

    **Recommendation:** Deferred hardening: extend real public-lifecycle failure input to worker-thread error acknowledgment, and establish a reliable external cleanup-failure input. No application-service replacement is needed for stopped-manager behavior.

    **Status:** Deferred at the user’s request; completed local portions remain recorded in the registry.

- **70. Packaged-default generations:** Only inherited packaged-default changes versus an explicit equal override remain unverified across template generations. Exact changing-source backup bytes, interrupted first-import recovery and real post-backup global-reset conflict now pass.

    **Recommendation:** Agree how to supply a different packaged-default generation without replacing the settings store or silently modifying shipped product inputs.

    **Status:** Deferred at the user’s request; completed local portions remain recorded in the registry.

- **71. Provider normalization helper contracts:** Actual Anthropic/Google structured/text/list/detail failures, message bounds, standard credential redaction and original producer-log secrecy now pass. Arbitrary explicit-redaction helper arguments, synthetic wrapped provider attribution and cyclic exception graphs remain a separate public-helper contract decision.

    **Recommendation:** Review whether these synthetic helper inputs are supported behavior worth retaining; app-level provider error/secrecy evidence is complete for the exercised external SDK shapes.

    **Status:** Deferred at the user’s request; completed local portions remain recorded in the registry.
