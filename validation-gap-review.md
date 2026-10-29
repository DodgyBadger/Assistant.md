# Remaining validation gaps

8 deferred items remain. Completed items have been removed; item 3 is tracked in the external To Do list. Numbers below are the original review IDs. The implemented practical alternatives and their explicit evidence limits are recorded in `validation/registry/pytest-gaps.json`.

- **23. Gmail connection UI behavior:** Review save-before-authorization behavior and missing-compose warnings in the browser. API scope and readiness checks do not establish rendered UI behavior.

    **Recommendation:** **Defer to the browser work.** Keep save-before-authorization and actionable scope warnings as user-visible requirements. Replace JavaScript-source, CSS-class and exact-copy assertions with browser journeys when that capability is resumed.
    - Agreed

- **31. Content-import UI behavior:** Test rendered import interactions and warnings when browser work resumes. API and tool tests do not replace the old JavaScript, CSS and source-text checks.

    **Recommendation:** **Defer to the browser work and group with item 23.** Test visible import progress, warnings, errors and user actions. Retire incidental CSS/JavaScript implementation checks once equivalent user-visible behavior is covered or explicitly judged unnecessary.
    - Approved

    **Current status:** Deferred by the approved recommendation. Your approval here was agreement to leave rendered browser behavior for the browser-testing work, which remains paused.

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

    **Current status:** Deferred by the approved recommendation. Your approval here was agreement to resume these checks with browser infrastructure. Successful Chromium sessions and finite-memory resource tests were not part of this cleanup.

- **62. Old adapter versus current evidence capture:** Review the old requirement to rewrite the application’s original log with redacted values. Current capture preserves original observations and redacts exported evidence; the old adapter self-test still fails that expectation.

    **Recommendation:** **Retire the requirement that the harness rewrite producer logs.** Preserve original observations and redact exported evidence. Separately verify that production code does not emit credentials in the first place, so harness redaction cannot hide an application logging defect.
    - Defer - I want to understand better

- **63. Additional isolation policies:** Decide whether fresh-process, environment-whitelist and `HOME`/temporary-directory isolation are warranted beyond the implemented safeguards. Core now has Python egress restrictions, explicit subprocess admission, controller-enforced deadlines and inherited-session cleanup. Complete native/child egress containment, credential-file isolation and deliberately detached process ownership remain outside the current guarantees.

    **Recommendation:** **Retain the current bounded safeguards and review stronger isolation only against a concrete contamination or containment requirement.** Keep isolated data/system roots, explicit test credentials, Python outbound restrictions, subprocess admission, cleanup quarantine and controller deadlines. Review `HOME`/temporary-directory replacement and stronger OS containment together when discussing this item; do not treat existing guards as an OS sandbox.
    - Defer - I want to understand better
