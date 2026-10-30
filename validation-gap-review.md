# Remaining validation gaps

12 review items remain; item 3 remains separately tracked in the external product To Do list. Completed local work has been removed from this list; numbers are stable review IDs. The user authorized clear local cases while excluding real advanced shell, access to other containers and uncertain contracts. Earlier cooperative/deferred decisions remain unchanged. Exact canonical assertions, partial proof and execution evidence are recorded in `validation/registry/backend-migration.json` and `validation/registry/pytest-gaps.json`.

- **37. Gmail account-specific token routing:** Verify that explicitly selecting each Gmail account sends that account’s token. Current transport fixtures use the saved default grant; selected-account metadata alone is insufficient.

    **Recommendation:** **Keep, high priority.** Exercise the actual selected-account path with two different grants and capture the outgoing authorization for each selection. Avoid a factory that substitutes the default grant; change composition only if investigation shows the real path cannot be exercised or is incorrect.
    - Defer to the end - I want to address this cooperatively

- **44. Remaining shell and MCP-over-SSH behavior:** A real local SSH endpoint now verifies successful command/stdin/stdout/stderr/exit transport, authenticated cached readiness and pinned-host-key rejection before authentication. MCP stdio initialization/tool calls/root forwarding/closure, client-key rejection, capacity/cancellation and remaining failure paths still need proof.

    **Recommendation:** Extend the shared local SSH fixture for these application contracts. No advanced-shell image or other container is required. Keep deployed forced-command restrictions and remote descendant cleanup separate; the controlled endpoint does not certify them.
    - Original decision: Defer real advanced-shell infrastructure. Local SSH pilot approved and passed on 2026-10-30.

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

All 93 contract groups across the 18 retained backend sources are reconciled: 71 covered, two retired and 20 partial. These are contract-group counts, not scenario or code-coverage percentages. Thirty-nine distinct targeted cases passed with completed cleanup; the full core suite remains stopped. Authentication, typed decisions, search failures, shipped defaults and local shell configuration/bootstrap items are complete and removed. Original backend sources remain retained; per-file acceptance is recorded in the registry.

- **65. Chat preparation and deferred instructions:** Primary/deferred preparation failure after MCP acquisition still depends on the advanced-shell readiness failure boundary used by the old tests. Deferred instruction ordering lacks a requirements-backed semantic output oracle; literal guidance copy is not being frozen.

    **Recommendation:** Leave pending: the shell-dependent preparation path is excluded and the instruction contract needs clarification.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **67. Import post-publication failure:** Only the narrow post-publication binary-write fault remains: a binary artifact is partially published before the writer raises. Prior Markdown cleanup, user-edit preservation, OCR allocated namespaces and job-finalization compensation now pass.

    **Recommendation:** Agree an honest filesystem/process fault input for the post-write window before adding this case; do not replace application storage methods.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **68. Future managed-catalog generations:** Hypothetical successive managed-catalog generations, alternate promotion/removal across those generations and a supported packaged-catalog fixture remain undecided. Historical family names, shipped SDK profiles, public custom rename/promotion, shared-alias failures and simultaneous cold loads now pass.

    **Recommendation:** Discuss the value and supported input contract for future packaged-catalog generations; public custom renames are not equivalent proof.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **69. Remaining MCP failure and capacity boundaries:** Real advanced-shell stdio capacity/serialization and HTTP progress are excluded. Narrow pre-DNS credential-capture timing, real cleanup-failure telemetry and synthetic private synchronous invalidation errors lack an agreed supported failure boundary. Partial snapshot cancellation, post-connect endpoint/token changes, forged/stale record rejection and worker-thread invalidation now pass.

    **Recommendation:** Retain these exact boundaries; successful SSH needs separate infrastructure, and cleanup/error timing needs a reliable external input rather than substituted application clients.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **70. Packaged-default generations:** Only inherited packaged-default changes versus an explicit equal override remain unverified across template generations. Exact changing-source backup bytes, interrupted first-import recovery and real post-backup global-reset conflict now pass.

    **Recommendation:** Agree how to supply a different packaged-default generation without replacing the settings store or silently modifying shipped product inputs.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.

- **71. Provider normalization helper contracts:** Actual Anthropic/Google structured/text/list/detail failures, message bounds, standard credential redaction and original producer-log secrecy now pass. Arbitrary explicit-redaction helper arguments, synthetic wrapped provider attribution and cyclic exception graphs remain a separate public-helper contract decision.

    **Recommendation:** Review whether these synthetic helper inputs are supported behavior worth retaining; app-level provider error/secrecy evidence is complete for the exercised external SDK shapes.

    **Status:** Retained under the user’s exclusions; completed local portions are recorded in the registry.
