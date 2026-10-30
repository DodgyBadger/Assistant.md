# Remaining validation gaps

10 review items remain; item 3 remains separately tracked in the external product To Do list. Completed local work has been removed from this list; numbers are stable review IDs. The user authorized clear local cases while excluding real advanced shell, access to other containers and uncertain contracts. Other deferred decisions remain unchanged. Exact canonical assertions, partial proof and execution evidence are recorded in `validation/registry/backend-migration.json` and `validation/registry/pytest-gaps.json`.

- **48. OAuth runtime acknowledgment failure:** Only post-commit runtime invalidation acknowledgment failure remains: saved OAuth state followed by HTTP503 committed=true/retry_safe=false and safe error telemetry. The original tests injected a private manager exception; a supported real failure input has not been agreed. Actual discovery/start/callback, state recovery, replay, expiry and completion deadlines, supersede/disconnect, grant revocation races, failed durable supersede retry and overlapping shutdown now pass against a real local OAuth/MCP provider.

    **Recommendation:** Defer this narrow acknowledgment-failure branch with the other uncertain runtime error-injection cases. Do not substitute the application manager solely to manufacture an error; the security-critical live authorization lifecycle now has deterministic real-service evidence.

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

All 93 contract groups across the 18 retained backend sources are reconciled: 78 covered, two retired and 13 partial. These are contract-group counts, not scenario or code-coverage percentages. The backend closure pass verified 39 distinct targeted cases; the subsequent SSH implementation verified 17 SSH cases and four existing MCP regression cases, all with completed cleanup; the full core suite remains stopped. Authentication, typed decisions, search failures, shipped defaults, local shell configuration/bootstrap and app-side SSH/MCP transport items are complete and removed. Original backend sources remain retained; per-file acceptance is recorded in the registry. The account/OAuth priority follow-up verified 25 distinct targeted/regression cases with completed cleanup; selected-account routing and the pre-DNS credential-binding race are complete. OAuth item 48 now contains only the uncertain post-commit acknowledgment-failure branch.

- **65. Chat preparation and instruction composition:** Semantic instruction inclusion/order for advanced-shell availability and deferred resumption still needs a requirements-backed application-output oracle. Exact flight-card copy and fake registration counters are not being frozen. Primary/deferred preparation cleanup needs an agreed real failure input: the original tests injected a shell-resolution exception, whereas actual SSH failures normally produce sanitized unavailability.

    **Recommendation:** Clarify the instruction/preparation contracts and an honest post-acquisition failure input. Do not substitute app shell services or create synthetic agent failures solely to reproduce the old mocks.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **67. Import post-publication failure:** Only the narrow post-publication binary-write fault remains: a binary artifact is partially published before the writer raises. Prior Markdown cleanup, user-edit preservation, OCR allocated namespaces and job-finalization compensation now pass.

    **Recommendation:** Agree an honest filesystem/process fault input for the post-write window before adding this case; do not replace application storage methods.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **68. Future managed-catalog generations:** Hypothetical successive managed-catalog generations, alternate promotion/removal across those generations and a supported packaged-catalog fixture remain undecided. Historical family names, shipped SDK profiles, public custom rename/promotion, shared-alias failures and simultaneous cold loads now pass.

    **Recommendation:** Discuss the value and supported input contract for future packaged-catalog generations; public custom renames are not equivalent proof.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **69. Remaining MCP race and error boundaries:** Only cleanup-failure telemetry at a supported real failure boundary and private synchronous invalidation error propagation remain. The pre-DNS credential-binding race is now proved with held OS DNS, real encrypted credentials/configuration and captured HTTP authorization. Earlier stdio capacity/cancellation, HTTP progress, snapshot cancellation and worker-thread invalidation evidence remains valid.

    **Recommendation:** Defer these remaining error-injection branches until a supported real failure input or explicit public contract is agreed; credential binding no longer requires follow-up.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **70. Packaged-default generations:** Only inherited packaged-default changes versus an explicit equal override remain unverified across template generations. Exact changing-source backup bytes, interrupted first-import recovery and real post-backup global-reset conflict now pass.

    **Recommendation:** Agree how to supply a different packaged-default generation without replacing the settings store or silently modifying shipped product inputs.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **71. Provider normalization helper contracts:** Actual Anthropic/Google structured/text/list/detail failures, message bounds, standard credential redaction and original producer-log secrecy now pass. Arbitrary explicit-redaction helper arguments, synthetic wrapped provider attribution and cyclic exception graphs remain a separate public-helper contract decision.

    **Recommendation:** Review whether these synthetic helper inputs are supported behavior worth retaining; app-level provider error/secrecy evidence is complete for the exercised external SDK shapes.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.
