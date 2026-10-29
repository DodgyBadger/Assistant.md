# Remaining validation gaps

14 items remain: six approved items with practical alternatives proposed below and eight deferred items. The 48 completed items have been removed; item 3 was moved to the external To Do list. Numbers below are the original review IDs, retained for cross-references.

- **4. Hard timeouts and detached child processes:** The worker watchdog and inherited-process cleanup are implemented and tested. A true deadline for native code holding the GIL, plus cleanup guarantees for deliberately detached sessions, remain unresolved.

    **Recommendation:** **Keep, high priority; combine with item 63.** Establish bounded process-level execution and cleanup deadlines, quarantine an uncertain worker, and terminate its owned descendant processes. Prefer existing pytest-compatible tooling and a small ownership policy over rebuilding the old supervisor.
    - Approved

    **Current status:** Partially completed; approved work remains. The watchdog and inherited-session cleanup pass, but a native call holding the GIL can prevent the Python watchdog from running, and a detached child can escape session ownership. Completing this needs an external process supervisor and stronger descendant ownership. This workspace has a read-only cgroup mount and no available container runtime; those guarantees have not been established.
    - We're already in a container, so testing this may be impactical. What is the next best?

    **Next-best recommendation:** Use the existing pytest controller to enforce a worker deadline from outside the worker process. A worker holding the GIL cannot prevent its controller from terminating it. Keep current owned-session cleanup and quarantine uncertain workers. Verify this with a deliberately stuck child worker and a passing replacement; no nested container is needed. Explicitly leave deliberately detached descendants outside the guarantee and disallow detachment in ordinary scenarios. Do not rebuild a general process supervisor to close that last edge case. This is a proposed controller capability, not one already implemented.

- **12. Importing public URLs:** Verify successful remote fetching, destination selection, provenance, PDF detection from content, and extensionless URLs. Local imports and outbound-policy rejection are covered.

    **Recommendation:** **Keep.** Build a reusable controlled HTTP/HTTPS fixture that exercises the real public-address admission and fetch path, including destination, provenance and content-based PDF detection. Preserve SSRF checks; avoid creating a production localhost exemption solely to make these tests pass.
    - Approved

    **Current status:** Approved, not completed. A loopback server is rejected by the real public-address policy. Successful fetching needs a controlled service reachable through that policy, including PDF and extensionless responses. No suitable isolated public-address fixture was available; production admission was not weakened to manufacture a passing result. This shares infrastructure with item 45.
    - Basically same as above - spinning up a live service may be impractical. Recommend next best.

    **Next-best recommendation:** Use a temporary local HTTP server, started and stopped by the scenario fixture, with real curl, import processing, destination selection and durable provenance. Introduce one explicit composition seam for URL admission: the fixture policy admits only its exact loopback origin and rejects other private targets; production keeps its strict public-only policy. Apply that policy consistently at initial admission and every redirect. Separately exercise the unchanged production policy with accepted/rejected address inputs and redirect rejection. This deliberately substitutes the environmental admission boundary; it does not prove successful public-network deployment. If that limited substitution is unacceptable, retain the public-success gap rather than build privileged infrastructure.

- **23. Gmail connection UI behavior:** Review save-before-authorization behavior and missing-compose warnings in the browser. API scope and readiness checks do not establish rendered UI behavior.

    **Recommendation:** **Defer to the browser work.** Keep save-before-authorization and actionable scope warnings as user-visible requirements. Replace JavaScript-source, CSS-class and exact-copy assertions with browser journeys when that capability is resumed.
    - Agreed

- **27. MCP OAuth guarded transport:** Verify a successful request through the guarded public-address transport. Client configuration and storage checks do not establish the full socket and DNS boundary.

    **Recommendation:** **Keep as part of the security transport work in item 45.** Exercise the actual socket backend and OAuth-discovered endpoints with controlled DNS/TLS infrastructure. Request hooks and client flags remain useful checks, but successful policy-compliant transport needs its own evidence.
    - Approved

    **Current status:** Approved, not completed. Existing client and storage checks do not exercise successful guarded public transport. This depends on the controlled DNS/TLS and address-routing fixture in item 45; no replacement transport or admission bypass was counted as proof.

    **Next-best recommendation:** Use a temporary local HTTPS MCP/OAuth server and the real guarded HTTP transport. Correction to the previous status: MCP already permits local HTTPS, so a public server and isolated address routing are unnecessary for successful transport. Provide a fixture-owned CA through an explicit SSL-context composition seam; retain certificate and hostname verification. Assert actual requests, original Host, successful responses and closure. Cover public/private classification separately. Authorization-lifecycle work in item 48 remains deferred.

- **31. Content-import UI behavior:** Test rendered import interactions and warnings when browser work resumes. API and tool tests do not replace the old JavaScript, CSS and source-text checks.

    **Recommendation:** **Defer to the browser work and group with item 23.** Test visible import progress, warnings, errors and user actions. Retire incidental CSS/JavaScript implementation checks once equivalent user-visible behavior is covered or explicitly judged unnecessary.
    - Approved

    **Current status:** Deferred by the approved recommendation. Your approval here was agreement to leave rendered browser behavior for the browser-testing work, which remains paused.

- **36. Core child-process egress and credential-file isolation:** Core Python requests are loopback-only, ambient SDK credentials are removed, and real denial controls pass. Complete network containment for child/native processes such as curl, and review access to undeclared credential files. Mixed live/core invocations need separate processes because the Python guard is irreversible.

    **Recommendation:** **Make this an early priority.** Enforce test-owned outbound restrictions that allow only declared deterministic models and controlled test services, and exclude ambient real credentials from core execution. Include child processes and automatic authoring; prove that an accidental external dispatch fails before connecting, while explicit live experiments remain separate.
    - Approved

    **Current status:** Partially completed; approved work remains. Core Python outbound requests are restricted and ambient SDK credentials are removed. Python audit hooks do not contain native or child-process networking or access to credential files. Stronger OS isolation is still needed: network namespace creation was denied in this workspace, and no container runtime or sandbox tool was available. Mixed live/core execution also still needs separate processes.
    - Same as others. Recommend next best.

    **Next-best recommendation:** Keep the Python egress guard and explicit test credentials, and add fail-closed admission for subprocess launches: core may launch only declared fixture-owned commands. Permit required real tools such as curl with bounded arguments and fixture destinations, checking redirects at the real fetch layer; unknown commands fail before launch. This reduces accidental live dispatch but is not an OS sandbox: an admitted executable or native extension could still connect or read mounted files. Use the existing disposable environment and secret-free CI as the practical containment boundary. Keep full native/child egress and credential-file isolation explicitly unsupported unless a concrete requirement justifies stronger infrastructure; do not present a launch allowlist as a security guarantee.

- **37. Gmail account-specific token routing:** Verify that explicitly selecting each Gmail account sends that account’s token. Current transport fixtures use the saved default grant; selected-account metadata alone is insufficient.

    **Recommendation:** **Keep, high priority.** Exercise the actual selected-account path with two different grants and capture the outgoing authorization for each selection. Avoid a factory that substitutes the default grant; change composition only if investigation shows the real path cannot be exercised or is incorrect.
    - Defer to the end - I want to address this cooperatively

- **38. Mixed and binary web extraction:** Verify preservation of successful items alongside rejected URLs, and rejection of binary PDF responses through the actual extraction path. Policy rejection and low-level transport are covered separately.

    **Recommendation:** **Keep.** Use the shared controlled network fixture to return one successful document, one policy-rejected target and one binary PDF. Verify per-item preservation and typed rejection without disabling public-target validation.
    - Approved

    **Current status:** Approved, not completed. Actual mixed-result and binary-response extraction depends on the admitted public-fetch fixture needed by items 12 and 45. Current loopback services cannot traverse that policy, so low-level transport checks were not presented as end-to-end extraction coverage.

    **Next-best recommendation:** Share the local-server and explicit fixture admission seam proposed for item 12. Serve real HTML, a real PDF body and a redirect to a disallowed destination. Run the actual extraction service and curl strategy, then assert preserved successful content and per-item typed failures. Also run the unchanged production-policy rejection controls. This proves extraction behavior with controlled URL admission; successful public-network composition remains a separately acknowledged limit.

- **44. Successful advanced-shell MCP execution:** Verify actual SSH launch configuration, root forwarding and client closure. Configuration gates and refused-connection behavior pass; successful SSH execution lacks local test infrastructure.

    **Recommendation:** **Defer to explicit infrastructure work.** Establish a reusable local SSH/container fixture, then verify actual launch arguments, roots and connection closure. Keep current admission/refusal checks in core; do not count an unreachable SSH endpoint as successful execution coverage.
    - Defer, I'm not convinced we're going to be able to adequately test this through scenarios.

- **45. MCP DNS and TLS boundaries:** Cover DNS rebinding, multiple-address retry budgets, cancellation during DNS resolution, and TLS hostname/SNI verification. Numeric-address policy and pinned HTTP sockets are covered.

    **Recommendation:** **Keep, high priority.** These are explicit security contracts, not incidental implementation details. Build shared local DNS/TLS fixtures that observe numeric connection targets, original-host certificate/SNI behavior, rebinding rejection, cancellation and total retry budgets; do not weaken the transport policy to make them testable.
    - Approved

    **Current status:** Approved, not completed. Completing this needs controlled DNS answers, numeric socket routing and a TLS server/certificate fixture while preserving the original public hostname. This workspace could not create a network namespace and had no container runtime or suitable isolated address-routing fixture. Existing pinned-socket checks remain useful, but rebinding, original-host TLS/SNI and the full retry/cancellation contracts remain unproved.
    - Same as above - recommend next best

    **Next-best recommendation:** Split this into real local transport scenarios and a small set of focused security-boundary tests. Local HTTPS with a fixture CA can prove original-host SNI, certificate rejection and real request/response behavior without privileged infrastructure. For DNS rebinding, ordered numeric targets and total retry/cancellation budgets, supply controlled resolver answers and a recording/fault-producing network delegate through explicit seams while keeping the actual MCP policy/backend logic. The backend already accepts a delegate; resolver and SSL-context composition still need review. These narrow doubles are a deliberate exception to the no-mocks preference because they control environmental failures rather than fabricate application outcomes. Preserve separate real-socket checks and do not call this complete end-to-end public DNS coverage. No external DNS service is required.

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

- **63. Old runner isolation policies:** Decide which fresh-process, environment-whitelist, `HOME`/temporary-directory isolation and hard-timeout policies must carry forward. Current pytest workers do not automatically establish those guarantees; this overlaps item 4.

    **Recommendation:** **Merge with item 4 and allow worker reuse only after confirmed cleanup.** Keep isolated data/system roots, explicit test credentials, outbound restrictions, cleanup quarantine and process-level hang protection. Do not restore blanket per-case process/HOME/temp replacement unless a concrete contamination case requires it.
    - Defer - I want to understand better
