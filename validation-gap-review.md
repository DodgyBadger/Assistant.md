1. **Settings conflict responses:** Verify that a real concurrent settings change produces the expected HTTP 409 response. The old test injected a repository conflict.

	 **Recommendation:** **Keep.** Add a real stale-revision settings request and assert HTTP 409, preservation of the newer settings, and a usable subsequent request. Retire the private save patch once that behavior is covered.
	 - Approved

4. **Hard timeouts and detached child processes:** The worker watchdog and inherited-process cleanup are implemented and tested. A true deadline for native code holding the GIL, plus cleanup guarantees for deliberately detached sessions, remain unresolved.

    **Recommendation:** **Keep, high priority; combine with item 63.** Establish bounded process-level execution and cleanup deadlines, quarantine an uncertain worker, and terminate its owned descendant processes. Prefer existing pytest-compatible tooling and a small ownership policy over rebuilding the old supervisor.
    - Approved

12. **Importing public URLs:** Verify successful remote fetching, destination selection, provenance, PDF detection from content, and extensionless URLs. Local imports and outbound-policy rejection are covered.

    **Recommendation:** **Keep.** Build a reusable controlled HTTP/HTTPS fixture that exercises the real public-address admission and fetch path, including destination, provenance and content-based PDF detection. Preserve SSRF checks; avoid creating a production localhost exemption solely to make these tests pass.
    - Approved

16. **Future managed-model catalog changes:** Verify catalog renames and update collisions against a changed future template. Current catalog migration and conflict handling are covered.

    **Recommendation:** **Keep a narrower, data-driven upgrade scenario.** Load explicit old/new catalog fixtures through the real configuration path and assert alias preservation, collision refusal and useful activity. Retire assumptions about hypothetical future vendor renames and private template patches.
    - Approved

23. **Gmail connection UI behavior:** Review save-before-authorization behavior and missing-compose warnings in the browser. API scope and readiness checks do not establish rendered UI behavior.

    **Recommendation:** **Defer to the browser work.** Keep save-before-authorization and actionable scope warnings as user-visible requirements. Replace JavaScript-source, CSS-class and exact-copy assertions with browser journeys when that capability is resumed.
    - Agreed

24. **Replay retention edge cases:** API cursor expiry, compact replay, reasoning/tool privacy and actual fork failures are covered. Verify full terminal-task eviction and cursor expiry during SSE admission.

    **Recommendation:** **Keep, high priority.** Add API-level replay/expiry cases using controlled retention inputs, real tool history and actual fork failures. Assert an honest resync/expiry result, no partial fork or future-context leakage, and correct reasoning/tool visibility; retain the lower-level buffer tests as supporting evidence.
    - Approved

25. **Cancellation during active preflight:** Accepted queue approval policy and cancellation before dispatch are covered. Cancel a real request while its preflight work is already executing, and prove that no model or tool work escapes afterward.

    **Recommendation:** **Keep, high priority.** Verify that queued work uses the approval policy accepted at submission and cannot silently gain permissions from later preference changes. Cancel a real queued/preflight workload and observe that no tool or model work escapes afterward; retire private preparation-call counters.
    - Approved

27. **MCP OAuth guarded transport:** Verify a successful request through the guarded public-address transport. Client configuration and storage checks do not establish the full socket and DNS boundary.

    **Recommendation:** **Keep as part of the security transport work in item 45.** Exercise the actual socket backend and OAuth-discovered endpoints with controlled DNS/TLS infrastructure. Request hooks and client flags remain useful checks, but successful policy-compliant transport needs its own evidence.
    - Approved

29. **Unexpected chat exceptions:** Verify generic unexpected failures during preparation and streaming, including their diagnostics and recovery markers. Real image-validation and provider errors exercise different branches.

    **Recommendation:** **Keep representative unexpected-failure coverage.** Use a genuine failure at each materially different preparation/streaming boundary and verify failed-task state, safe diagnostics, recovery markers and a usable next turn. Retire exact exception types and text that existed only because of the old fake agent.
    - Approved

31. **Content-import UI behavior:** Test rendered import interactions and warnings when browser work resumes. API and tool tests do not replace the old JavaScript, CSS and source-text checks.

    **Recommendation:** **Defer to the browser work and group with item 23.** Test visible import progress, warnings, errors and user actions. Retire incidental CSS/JavaScript implementation checks once equivalent user-visible behavior is covered or explicitly judged unnecessary.
    - Approved

34. **Automatic retry recovery branches:** Cover stalled streams, slow active tools, diagnostic truncation, checkpoint replay, rollback-and-restart, and retry with rollback disabled. Ordinary partial-stream retry and shared request budgets are covered.

    **Recommendation:** **Keep, high priority.** Complete the real idle-stream, active-tool, checkpoint replay and rollback recovery scenarios. Verify retry budgets, persisted history, mutation effects and safe diagnostics; share fixtures with the active cancellation and compaction cases rather than reproducing a large fake-agent matrix.
    - Approved

36. **Preventing live models in core tests:** Complete the model-call audit and choose enforcement that prevents accidental external inference. Current tests use deterministic models or local providers, but there is no universal fail-closed guard.

    **Recommendation:** **Make this an early priority.** Enforce test-owned outbound restrictions that allow only declared deterministic models and controlled test services, and exclude ambient real credentials from core execution. Include child processes and automatic authoring; prove that an accidental external dispatch fails before connecting, while explicit live experiments remain separate.
    - Approved

37. **Gmail account-specific token routing:** Verify that explicitly selecting each Gmail account sends that account’s token. Current transport fixtures use the saved default grant; selected-account metadata alone is insufficient.

    **Recommendation:** **Keep, high priority.** Exercise the actual selected-account path with two different grants and capture the outgoing authorization for each selection. Avoid a factory that substitutes the default grant; change composition only if investigation shows the real path cannot be exercised or is incorrect.
    - Defer to the end - I want to address this cooperatively

38. **Mixed and binary web extraction:** Verify preservation of successful items alongside rejected URLs, and rejection of binary PDF responses through the actual extraction path. Policy rejection and low-level transport are covered separately.

    **Recommendation:** **Keep.** Use the shared controlled network fixture to return one successful document, one policy-rejected target and one binary PDF. Verify per-item preservation and typed rejection without disabling public-target validation.
    - Approved

44. **Successful advanced-shell MCP execution:** Verify actual SSH launch configuration, root forwarding and client closure. Configuration gates and refused-connection behavior pass; successful SSH execution lacks local test infrastructure.

    **Recommendation:** **Defer to explicit infrastructure work.** Establish a reusable local SSH/container fixture, then verify actual launch arguments, roots and connection closure. Keep current admission/refusal checks in core; do not count an unreachable SSH endpoint as successful execution coverage.
    - Defer, I'm not convinced we're going to be able to adequately test this through scenarios.

45. **MCP DNS and TLS boundaries:** Cover DNS rebinding, multiple-address retry budgets, cancellation during DNS resolution, and TLS hostname/SNI verification. Numeric-address policy and pinned HTTP sockets are covered.

    **Recommendation:** **Keep, high priority.** These are explicit security contracts, not incidental implementation details. Build shared local DNS/TLS fixtures that observe numeric connection targets, original-host certificate/SNI behavior, rebinding rejection, cancellation and total retry budgets; do not weaken the transport policy to make them testable.
    - Approved

48. **MCP OAuth authorization lifecycle:** Test actual discovery and authorization start, live callback rejection and recovery, completion timeout, superseding attempts, live disconnect and failed supersede recovery. Current tests begin with saved pending records.

    **Recommendation:** **Keep.** Add a real local authorization-discovery/MCP server and test start through callback completion, supersede, disconnect, expiry and durable-failure retry. Keep the saved-pending tests as restart coverage, and assert operation identity and cancellation reasons through actual activity records.
    - Defer - I want to understand better

50. **Remaining provider classification and attribution:** Configured-provider503 classification and actual bearer routing are covered. Complete the concrete overload/idle/connection matrix and OpenRouter model-path attribution without synthetic SDK-class equivalence.

    **Recommendation:** **Keep application error semantics and attribution behavior; retire arbitrary SDK-class equivalence.** Cover actual overload, idle, connection and HTTP-status inputs with retry/category assertions where they affect control flow, plus headers observed at a local provider. Consolidate overlapping cases with items 17, 33 and 46.
    - Approved

54. **Browser resource admission:** Verify actual successful-session concurrency limits and low-memory refusal when browser work resumes. Call budgets are covered without launching Chromium.

    **Recommendation:** **Defer with browser infrastructure work.** Use a finite-memory container and held local pages to test real concurrency, memory refusal before launch and slot release after cancellation. Retire the fake browse counter once the actual resource guarantees are observable.
    - Approved

55. **Fork snapshot consistency and hydration:** Verify the precise concurrent source-rewrite boundary during a fork, and decide whether the old prohibitions on loading certain historical rows are required contracts.

    **Recommendation:** **Keep snapshot consistency and future-evidence exclusion, retire private hydration prohibitions as standalone contracts.** Use actual concurrent database operations and verify that each fork reflects one coherent source revision. If large-history performance is a real requirement, express a bounded resource contract rather than forbidding a particular internal fetch method.
    - Approved

56. **Remaining delegate boundaries:** Cover cancellation before attachment, private observer counters, distinct attention issues, several diagnostic/redaction helpers, and synthetic internal timeout classification. Parent completion after a child request limit also needs direct proof.

    **Recommendation:** **Keep the remaining user-visible cancellation, parent usability, safe handoff/audit and distinct-attention outcomes.** Replace private observer counters with real task/tool activity, and retire impossible-input or synthetic-exception helper assertions that protect no reachable behavior. Reuse the existing delegate workload fixtures rather than recreating its fake agent.
    - Approved

57. **Manual compaction through tools and failures:** Cover tool-initiated compaction, its parent task and returned result, plus remaining cancellation, failure, activity, no-op and exported-diagnostic branches.

    **Recommendation:** **Keep a real tool-initiated compaction journey and a compact failure matrix.** Verify parent/child relationships, the tool result, preserved history and subsequent chat usability. Reuse shared author-failure, cancellation and confidentiality cases instead of repeating every low-level projection in another large scenario.
    - Approved

58. **Specialized checkpoint boundaries:** Complete the boundary cases for checkpoint creation and tool-history timeline projection, filtering and paging. Review whether the deliberately injected display-metadata inconsistency should remain a test.

    **Recommendation:** **Keep canonical checkpoint, timeline and paging boundaries.** Consolidate overlapping history inputs with items 55 and 60. Retire forced private display-metadata drift unless it represents a supported persisted corruption state; test that real state directly if it does.
    - Approved

59. **Session-context upgrade races and recovery:** Cover plan limits, fork/start races, stale author results after append or deletion, contention with manual compaction, failure diagnostics, and shutdown/restart followed by successful retry.

    **Recommendation:** **Keep, prioritizing data-preservation races and restart recovery.** Use real gated authors and database mutations to verify stale append/delete/fork rejection, cancellation followed by restart/retry, and unchanged canonical history. Retire internal plan-shape assertions where existing outcome and configured-limit tests establish the same guarantee.
    - Approved

60. **Transcript tool errors and limits:** Complete tool-level output budgets, fragment continuation, query/title limits, cross-workspace access cases, real full-text-search failure diagnostics and untrusted-operation logging.

    **Recommendation:** **Keep permissions, output limits and actual tool continuation as durable contracts.** Add a compact real-tool boundary matrix for workspace/session access, fragment continuation, query/title bounds and FTS failure correlation. Reuse item 20’s confidentiality proof rather than manufacturing a special backend exception in every retrieval scenario.
    - Approved

62. **Old adapter versus current evidence capture:** Review the old requirement to rewrite the application’s original log with redacted values. Current capture preserves original observations and redacts exported evidence; the old adapter self-test still fails that expectation.

    **Recommendation:** **Retire the requirement that the harness rewrite producer logs.** Preserve original observations and redact exported evidence. Separately verify that production code does not emit credentials in the first place, so harness redaction cannot hide an application logging defect.
    - Defer - I want to understand better

63. **Old runner isolation policies:** Decide which fresh-process, environment-whitelist, `HOME`/temporary-directory isolation and hard-timeout policies must carry forward. Current pytest workers do not automatically establish those guarantees; this overlaps item 4.

    **Recommendation:** **Merge with item 4 and allow worker reuse only after confirmed cleanup.** Keep isolated data/system roots, explicit test credentials, outbound restrictions, cleanup quarantine and process-level hang protection. Do not restore blanket per-case process/HOME/temp replacement unless a concrete contamination case requires it.
    - Defer - I want to understand better
