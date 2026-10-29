1. **Settings conflict responses:** Verify that a real concurrent settings change produces the expected HTTP 409 response. The old test injected a repository conflict.

    **Recommendation:** **Keep.** Add a real stale-revision settings request and assert HTTP 409, preservation of the newer settings, and a usable subsequent request. Retire the private save patch once that behavior is covered.

2. **Secrets migration crash recovery:** Test abrupt process death during import, after import, and after renaming the source. Exception recovery is covered; actual crash recovery at those boundaries is not.

    **Recommendation:** **Keep, high priority.** Run the actual migration in a child process, terminate it at durable import/rename boundaries, then restart and verify that credentials are neither lost nor duplicated. Reuse a small crash fixture rather than adding a separate runner.

3. **Root-folder deletion policy:** Decide whether deletion using `.` or `./` should be rejected. The rejection requirement came from an evaluation exercise, rather than an established product specification.

    **Recommendation:** **Reject root deletion through ordinary file tools.** I recommend treating every normalized alias of the vault root as protected, including `.` and `./`. Any deliberate empty-vault operation should have its own explicit contract; this is a product decision for your approval, not an established requirement.

4. **Hard timeouts and child-process cleanup:** Decide and verify how hung tests and their descendant processes should be terminated. The new plugin does not currently provide the old runner’s guarantees.

    **Recommendation:** **Keep, high priority; combine with item 63.** Establish bounded process-level execution and cleanup deadlines, quarantine an uncertain worker, and terminate its owned descendant processes. Prefer existing pytest-compatible tooling and a small ownership policy over rebuilding the old supervisor.

5. **Concurrent writes to the same file:** Successful results and preservation of updates are covered. Actual non-overlap between active writes has not been observed.

    **Recommendation:** **Retire the strict non-overlap assertion as a separate requirement.** Keep tests for no lost updates, atomic file contents, consistent snapshots and correct mutation history. Require serialization only where it is necessary to deliver those guarantees, so a different correct implementation remains possible.

6. **Editing during a directory move:** Verify that a child-file edit waits correctly while its directory is being moved. Sequential move and stale-path rejection do not establish this.

    **Recommendation:** **Keep the contention behavior, clarify its contract.** A conflicting edit should either be serialized safely or explicitly rejected before writing; it must never disappear or affect the wrong path. Test a genuine overlapping move/edit and the resulting files and audit state rather than prescribing one waiting mechanism.

7. **Rollback of an artificially reactivated snapshot:** Decide whether recovery from a snapshot that was already rolled back, then manually reactivated with missing mutation history, is a supported corruption-recovery contract.

    **Recommendation:** **Retire this manufactured recovery state unless we can identify a supported migration or real crash that produces it.** Keep the existing real rollback conflict/retry coverage. Recoverability from arbitrarily edited internal records should not become an accidental product promise.

8. **Concurrent activity rollback:** Verify that only one restore executes while another rollback request arrives. Current tests establish correct public results, but not the original internal restore-count assertion.

    **Recommendation:** **Replace the internal call-count assertion.** Require one coherent durable rollback effect, with a concurrent request either idempotent or clearly rejected, and verify files, snapshots and activity afterward. Multiple internal restoration calls are acceptable only if they cannot duplicate or corrupt that effect.

9. **Guarded secret-write concurrency:** Verify the precise concurrency boundary between checking an encrypted secret and updating it. The old test paused a private database lookup.

    **Recommendation:** **Keep, high priority.** Verify that a stale guarded write cannot overwrite a newer secret or cross a credential generation. Use real connections and transaction contention; the exact private lookup pause is unnecessary once the state and authorization guarantees are established.

10. **Default context instructions:** Decide which missing-README advice and packaged policy instructions are durable behavior. Their inclusion and structure are covered, but their intended meaning is not independently tested.

    **Recommendation:** **Retire wording assertions and combine their review with items 18 and 30.** Keep conditional inclusion, instruction ordering and source-data boundaries as executable contracts. Review instructional intent as prose; a captured prompt does not establish that a model will obey it.

11. **Locked bootstrap with cold imports:** Investigate why some cold import orders create an unrelated chat database and migration backup. Existing passing cases use prewarmed modules and do not cover every startup order.

    **Recommendation:** **Investigate through a fresh production-style startup before deciding this is a bug.** Fix writes to the wrong root or locked access state. Independent migrations of other managed databases may be legitimate; retain a scenario for the approved cold-start behavior rather than requiring every startup to create no files.

12. **Importing public URLs:** Verify successful remote fetching, destination selection, provenance, PDF detection from content, and extensionless URLs. Local imports and outbound-policy rejection are covered.

    **Recommendation:** **Keep.** Build a reusable controlled HTTP/HTTPS fixture that exercises the real public-address admission and fetch path, including destination, provenance and content-based PDF detection. Preserve SSRF checks; avoid creating a production localhost exemption solely to make these tests pass.

13. **Import authority before job creation:** Verify that missing execution authority rejects an immediate import before creating a durable job. The old test injected failure into authority resolution.

    **Recommendation:** **Keep the authorization invariant.** Use real unauthenticated or unauthorized ingress and verify that no job is created. Retire the injected resolver-exception ordering assertion if that exception cannot arise through a supported request; retain the ordering check where it protects an actual reachable boundary.

14. **Batch-move compensation:** Verify rollback after failures partway through moving files or directories. Preflight rejection and ordinary successful moves do not establish compensation after partial completion.

    **Recommendation:** **Keep, high priority.** I recommend an all-or-nothing contract for these supported batch moves. Induce a genuine later-item filesystem or database failure and check restoration of files, directories, manifest and audit state, including protection of any newer external edits.

15. **Interrupted upload cleanup:** Verify that a real write interruption leaves no partial upload behind. Size limits, collisions and preflight checks are covered.

    **Recommendation:** **Keep.** Use a bounded filesystem failure after writing has begun and verify removal of partial files, preservation of existing destinations and an actionable failure result. A failure before the first byte is written does not replace this case.

16. **Future managed-model catalog changes:** Verify catalog renames and update collisions against a changed future template. Current catalog migration and conflict handling are covered.

    **Recommendation:** **Keep a narrower, data-driven upgrade scenario.** Load explicit old/new catalog fixtures through the real configuration path and assert alias preservation, collision refusal and useful activity. Retire assumptions about hypothetical future vendor renames and private template patches.

17. **Transient web-provider failures:** Verify real HTTP 503 handling, `Retry-After` values, and removal of private query text from provider errors. Missing-key behavior does not cover these failures.

    **Recommendation:** **Keep.** Exercise actual 503 and Retry-After responses plus private query text through a controlled provider transport. Share network infrastructure with items 12 and 38, and assert retry decisions and safe diagnostics rather than just exception construction.

18. **Session-map instruction wording:** Decide whether old assertions about exact authoring instructions and setting descriptions should become behavioral checks or be retired.

    **Recommendation:** **Retire exact instruction and description substrings.** Consolidate with items 10 and 30. Keep schema, provenance, readiness and conditional instruction composition tests; add a behavioral replacement only where there is a concrete durable decision to observe.

19. **Transcript index migration:** Test a genuinely old database without transcript indexes. Both the original and migrated tests created indexed messages before reinitializing the schema.

    **Recommendation:** **Keep.** Create the oldest supported database format without the transcript indexes, start the real migration path, and verify searchable preserved messages and repeatable startup. Avoid first creating those messages through modern index-maintaining writes.

20. **Private text in transcript-search errors:** Verify sanitization when a storage exception actually contains private text. Current real SQLite failures produce generic messages, so the stronger confidentiality claim remains unproved.

    **Recommendation:** **Keep the confidentiality requirement, simplify the proof.** Test the application-owned error projection with a private error input, alongside a real integration failure proving that the projection is used. Do not require a particular SQLite error to naturally echo a secret or patch the search implementation to manufacture that condition.

21. **Google connection transaction races:** Verify concurrent deletion, default replacement, client-identity changes, credential changes and token commits at the original transaction boundaries. Ordinary concurrency tests cover narrower cases.

    **Recommendation:** **Keep, high priority; consolidate the access-state races with items 22, 43 and 47.** Protect against stale operations restoring deleted connections, overwriting newer credentials or committing a grant for a changed identity. Use a compact set of real transactions and external-request barriers rather than preserving every private pause location.

22. **Google disconnect rollback:** Verify that failed cleanup during disconnect preserves the previous grant and credentials. Other credential-update rollback cases do not establish this branch.

    **Recommendation:** **Keep.** Cause a real disconnect cleanup transaction to fail and verify that connection metadata, credentials and grants remain mutually consistent, then verify successful retry. Separately preserve the existing distinction between a rolled-back mutation and a committed mutation whose runtime notification failed.

23. **Gmail connection UI behavior:** Review save-before-authorization behavior and missing-compose warnings in the browser. API scope and readiness checks do not establish rendered UI behavior.

    **Recommendation:** **Defer to the browser work.** Keep save-before-authorization and actionable scope warnings as user-visible requirements. Replace JavaScript-source, CSS-class and exact-copy assertions with browser journeys when that capability is resumed.

24. **Chat replay, expiry and fork failures:** Cover unresolved-tool fork suppression, projection rollback, cursor-expiry races, expired raw events, missing terminal retention, and reasoning/tool projections through the actual API.

    **Recommendation:** **Keep, high priority.** Add API-level replay/expiry cases using controlled retention inputs, real tool history and actual fork failures. Assert an honest resync/expiry result, no partial fork or future-context leakage, and correct reasoning/tool visibility; retain the lower-level buffer tests as supporting evidence.

25. **Queued chat approval and preflight:** Verify that queued work retains the approval policy accepted at submission, and that cancellation during preflight behaves correctly. Current tests prove narrower queue and model-invocation behavior.

    **Recommendation:** **Keep, high priority.** Verify that queued work uses the approval policy accepted at submission and cannot silently gain permissions from later preference changes. Cancel a real queued/preflight workload and observe that no tool or model work escapes afterward; retire private preparation-call counters.

26. **Delegated image delivery:** Observe image bytes reaching the intended model request. A successful delegate response and an available image fixture do not independently prove image consumption.

    **Recommendation:** **Keep.** Capture the actual child-provider request and compare its image payload or digest with the fixture bytes. This establishes delivery; keep image interpretation quality outside deterministic core scenarios.

27. **MCP OAuth guarded transport:** Verify a successful request through the guarded public-address transport. Client configuration and storage checks do not establish the full socket and DNS boundary.

    **Recommendation:** **Keep as part of the security transport work in item 45.** Exercise the actual socket backend and OAuth-discovered endpoints with controlled DNS/TLS infrastructure. Request hooks and client flags remain useful checks, but successful policy-compliant transport needs its own evidence.

28. **Workflow output-file ownership:** Determine whether a real workflow producer supplies nonempty `output_files`. The original injected them; current real execution paths return an empty list, although storage persistence is covered.

    **Recommendation:** **Retire the injected nonempty-result propagation assertion unless we identify a real producer.** Use existing file-mutation and durable audit records as evidence of actual workflow outputs. Decide separately whether the unused `output_files` field needs a producer or removal; do not invent one merely to preserve this test.

29. **Unexpected chat exceptions:** Verify generic unexpected failures during preparation and streaming, including their diagnostics and recovery markers. Real image-validation and provider errors exercise different branches.

    **Recommendation:** **Keep representative unexpected-failure coverage.** Use a genuine failure at each materially different preparation/streaming boundary and verify failed-task state, safe diagnostics, recovery markers and a usable next turn. Retire exact exception types and text that existed only because of the old fake agent.

30. **Chat guidance wording:** Review browser descriptions, usage-limit guidance, recovery-card instructions and unavailable-MCP instructions. Structural behavior is covered; exact editorial wording and its intended meaning are not.

    **Recommendation:** **Retire editorial wording assertions and consolidate with items 10 and 18.** Keep instruction-layer order, conditional inclusion, structured reason codes and actual enforcement. Guidance that matters but has no observable behavioral contract should receive prose review rather than brittle substring tests.

31. **Content-import UI behavior:** Test rendered import interactions and warnings when browser work resumes. API and tool tests do not replace the old JavaScript, CSS and source-text checks.

    **Recommendation:** **Defer to the browser work and group with item 23.** Test visible import progress, warnings, errors and user actions. Retire incidental CSS/JavaScript implementation checks once equivalent user-visible behavior is covered or explicitly judged unnecessary.

32. **Execution-task race windows:** Cover the exact transitions around starting, completing, failing, reserving tasks, parent termination and concurrency cleanup. Current real workloads establish broader outcomes, not every original interleaving.

    **Recommendation:** **Keep the task lifecycle invariants, reduce the scheduling-specific assertions.** Cover terminalization, cancellation, parent/child admission and cleanup with real workload barriers. Preserve a precise race regression where it protects a known failure; retire private method-count or pause requirements that add no distinct behavioral guarantee.

33. **Manual retry and legacy SDK errors:** Resolve whether raw OpenAI `APIError` should still classify as `transient_provider`. Real adapter errors normalize differently; transport-interruption recovery is covered.

    **Recommendation:** **Retire the raw SDK exception mapping as a compatibility promise unless a supported path still produces it.** Keep application-level retryability and recovery for actual transport and normalized provider failures. Preserve stable application categories where callers rely on them, rather than pinning behavior to an artificial SDK exception.

34. **Automatic retry recovery branches:** Cover stalled streams, slow active tools, diagnostic truncation, checkpoint replay, rollback-and-restart, and retry with rollback disabled. Ordinary partial-stream retry and shared request budgets are covered.

    **Recommendation:** **Keep, high priority.** Complete the real idle-stream, active-tool, checkpoint replay and rollback recovery scenarios. Verify retry budgets, persisted history, mutation effects and safe diagnostics; share fixtures with the active cancellation and compaction cases rather than reproducing a large fake-agent matrix.

35. **Cancellation during an active tool:** Verify cancellation while a tool itself is still running. Current coverage cancels while the model waits after the mutation has completed.

    **Recommendation:** **Keep, high priority.** Cancel a genuinely active tool or delegate after an observed side effect and before completion, then verify termination, rollback, settled history and a usable next turn. Reuse the existing controlled delegate/provider workloads where possible.

36. **Preventing live models in core tests:** Complete the model-call audit and choose enforcement that prevents accidental external inference. Current tests use deterministic models or local providers, but there is no universal fail-closed guard.

    **Recommendation:** **Make this an early priority.** Enforce test-owned outbound restrictions that allow only declared deterministic models and controlled test services, and exclude ambient real credentials from core execution. Include child processes and automatic authoring; prove that an accidental external dispatch fails before connecting, while explicit live experiments remain separate.

37. **Gmail account-specific token routing:** Verify that explicitly selecting each Gmail account sends that account’s token. Current transport fixtures use the saved default grant; selected-account metadata alone is insufficient.

    **Recommendation:** **Keep, high priority.** Exercise the actual selected-account path with two different grants and capture the outgoing authorization for each selection. Avoid a factory that substitutes the default grant; change composition only if investigation shows the real path cannot be exercised or is incorrect.

38. **Mixed and binary web extraction:** Verify preservation of successful items alongside rejected URLs, and rejection of binary PDF responses through the actual extraction path. Policy rejection and low-level transport are covered separately.

    **Recommendation:** **Keep.** Use the shared controlled network fixture to return one successful document, one policy-rejected target and one binary PDF. Verify per-item preservation and typed rejection without disabling public-target validation.

39. **MCP cleanup during failed bootstrap:** Verify that the real MCP manager starts and shuts down exactly once when startup fails. Successful repair and restart do not establish that exact lifecycle count.

    **Recommendation:** **Replace the exact start/shutdown invocation count.** Verify that failed startup leaves no live MCP clients or owned background work and permits a clean subsequent startup. Preserve exactly-once closure only where the resource/protocol requires it; the manager need not be constructed at a particular point in a future bootstrap design.

40. **Interrupted summary-data retirement:** Verify atomic rollback if migration fails after dropping tables but before recording completion, followed by successful retry. Successful retirement and dependency refusals are covered.

    **Recommendation:** **Keep, high priority.** Use a real SQLite constraint or trigger to fail migration bookkeeping after table changes, then verify transactional restoration and successful retry. Preserve unknown tables and canonical history throughout; this is an important destructive-migration guarantee.

41. **Automatic compaction admission failures:** Cover failures before compaction enters its normal domain logic, task-admission rejection, and missing or stopped runtime state.

    **Recommendation:** **Keep fail-soft automatic compaction.** Exercise genuine closed-runtime or rejected-admission inputs and verify that the completed chat remains successful, no task leaks, and usable context is preserved with a clear reason. Retire the requirement to inject a particular private callback failure.

42. **Interruption during an approved tool:** Verify cancellation or failure while a reviewed tool is active, settlement of missing replies, and prevention of replay on the next turn. Current coverage fails after the approved write completes.

    **Recommendation:** **Keep, high priority.** Interrupt an actually executing approved tool and verify rollback, an honest interrupted reply, terminal review state and no re-execution on the next turn. Share the workload fixture with item 35, but retain separate assertions for approval settlement.

43. **MCP mutation race windows:** Cover the original pre-lock credential and OAuth interleavings, plus process death inside the composed credential mutation before commit. Sequential stale-state checks and broader transaction crash tests are covered.

    **Recommendation:** **Consolidate with items 21 and 47.** Keep stale-grant/credential rejection and atomic metadata-plus-secret state through the composed service. Combine real transaction/crash evidence with a small number of meaningful stale-request races; retire the requirement to kill execution at a particular private instruction if it adds no distinct state guarantee.

44. **Successful advanced-shell MCP execution:** Verify actual SSH launch configuration, root forwarding and client closure. Configuration gates and refused-connection behavior pass; successful SSH execution lacks local test infrastructure.

    **Recommendation:** **Defer to explicit infrastructure work.** Establish a reusable local SSH/container fixture, then verify actual launch arguments, roots and connection closure. Keep current admission/refusal checks in core; do not count an unreachable SSH endpoint as successful execution coverage.

45. **MCP DNS and TLS boundaries:** Cover DNS rebinding, multiple-address retry budgets, cancellation during DNS resolution, and TLS hostname/SNI verification. Numeric-address policy and pinned HTTP sockets are covered.

    **Recommendation:** **Keep, high priority.** These are explicit security contracts, not incidental implementation details. Build shared local DNS/TLS fixtures that observe numeric connection targets, original-host certificate/SNI behavior, rebinding rejection, cancellation and total retry budgets; do not weaken the transport policy to make them testable.

46. **MCP timeout classification:** Resolve a real mismatch: delayed initialization returns `connection_failed`, while a total-deadline probe returned `http_error`. The old mocked test expects `timeout`.

    **Recommendation:** **Keep the application timeout contract and investigate a fix.** Classify expiry using the actual deadline and preserved exception causes so SDK cleanup cannot accidentally relabel a timeout as an HTTP error. Retain a separate genuine pre-deadline HTTP-error case; do not simply change the old expected status to the observed result.

47. **Google OAuth pending-state races:** Cover superseding authorization immediately before pending-state deletion, disconnect racing with credential replacement, and competing starts around pending-state writes.

    **Recommendation:** **Keep, high priority; consolidate with items 21 and 43.** Verify single-use pending-state consumption and preservation of a newer attempt or credential generation across competing start/disconnect/completion operations. Prefer actual transaction and external-request barriers over exact storage-method interception.

48. **MCP OAuth authorization lifecycle:** Test actual discovery and authorization start, live callback rejection and recovery, completion timeout, superseding attempts, live disconnect and failed supersede recovery. Current tests begin with saved pending records.

    **Recommendation:** **Keep.** Add a real local authorization-discovery/MCP server and test start through callback completion, supersede, disconnect, expiry and durable-failure retry. Keep the saved-pending tests as restart coverage, and assert operation identity and cancellation reasons through actual activity records.

49. **OpenAI device authorization:** Cover device-code start, polling, authorization and API-key fallback while connected. Manual OAuth completion works, but device endpoints currently point to the remote service.

    **Recommendation:** **Keep.** Make device transport endpoints configurable at the adapter boundary with production defaults preserved, then exercise a local device flow and the connected OAuth/API-key fallback matrix. Do not require a real account or remote authorization in core tests.

50. **Remaining API error classifications:** Cover non-status OpenAI errors, overload errors, stream-idle timeouts, connection failures and OpenRouter attribution headers. Real HTTP and SDK status-error classifications are covered.

    **Recommendation:** **Keep application error semantics and attribution behavior; retire arbitrary SDK-class equivalence.** Cover actual overload, idle, connection and HTTP-status inputs with retry/category assertions where they affect control flow, plus headers observed at a local provider. Consolidate overlapping cases with items 17, 33 and 46.

51. **Session-map author failure classification:** Verify generic private runtime-error sanitization and resolve another real mismatch: repeated invalid citations end in SDK retry exhaustion, whereas the old stub expects `SessionMapProvenanceError`.

    **Recommendation:** **Preserve the application-owned invalid-provenance meaning after retry exhaustion.** I recommend normalizing that outcome at the authoring boundary, retaining its cause internally, rather than exposing whichever SDK exception happens to escape. Also keep generic-error confidentiality and a deliberate diagnostic size bound; retire the incidental 100-character limit unless a real consumer requires it.

52. **Legacy session-map evidence repair:** Cover the complete sequence of rejecting invalid citations without changing the checkpoint, accepting valid historical evidence, and repairing pending evidence across later reductions.

    **Recommendation:** **Keep, high priority.** Build one complete reduction/repair journey from the oldest supported persisted map format. Verify that rejected citations preserve the previous checkpoint and raw history, valid evidence advances the map safely, and pending evidence is consumed correctly; avoid supporting every intermediate development schema by accident.

53. **Automatic retained-evidence compaction:** Verify automatic post-turn invocation and task-parent relationships. Explicit compaction proves the retained citation behavior, but not the automatic trigger.

    **Recommendation:** **Merge into the automatic post-turn scenario in item 61.** Verify that retained evidence reaches the real author, its citations survive persistence, and the result belongs to the expected task chain. A separate trigger scenario for every evidence variant would add maintenance without much additional assurance.

54. **Browser resource admission:** Verify actual successful-session concurrency limits and low-memory refusal when browser work resumes. Call budgets are covered without launching Chromium.

    **Recommendation:** **Defer with browser infrastructure work.** Use a finite-memory container and held local pages to test real concurrency, memory refusal before launch and slot release after cancellation. Retire the fake browse counter once the actual resource guarantees are observable.

55. **Fork snapshot consistency and hydration:** Verify the precise concurrent source-rewrite boundary during a fork, and decide whether the old prohibitions on loading certain historical rows are required contracts.

    **Recommendation:** **Keep snapshot consistency and future-evidence exclusion, retire private hydration prohibitions as standalone contracts.** Use actual concurrent database operations and verify that each fork reflects one coherent source revision. If large-history performance is a real requirement, express a bounded resource contract rather than forbidding a particular internal fetch method.

56. **Remaining delegate boundaries:** Cover cancellation before attachment, private observer counters, distinct attention issues, several diagnostic/redaction helpers, and synthetic internal timeout classification. Parent completion after a child request limit also needs direct proof.

    **Recommendation:** **Keep the remaining user-visible cancellation, parent usability, safe handoff/audit and distinct-attention outcomes.** Replace private observer counters with real task/tool activity, and retire impossible-input or synthetic-exception helper assertions that protect no reachable behavior. Reuse the existing delegate workload fixtures rather than recreating its fake agent.

57. **Manual compaction through tools and failures:** Cover tool-initiated compaction, its parent task and returned result, plus remaining cancellation, failure, activity, no-op and exported-diagnostic branches.

    **Recommendation:** **Keep a real tool-initiated compaction journey and a compact failure matrix.** Verify parent/child relationships, the tool result, preserved history and subsequent chat usability. Reuse shared author-failure, cancellation and confidentiality cases instead of repeating every low-level projection in another large scenario.

58. **Specialized checkpoint boundaries:** Complete the boundary cases for checkpoint creation and tool-history timeline projection, filtering and paging. Review whether the deliberately injected display-metadata inconsistency should remain a test.

    **Recommendation:** **Keep canonical checkpoint, timeline and paging boundaries.** Consolidate overlapping history inputs with items 55 and 60. Retire forced private display-metadata drift unless it represents a supported persisted corruption state; test that real state directly if it does.

59. **Session-context upgrade races and recovery:** Cover plan limits, fork/start races, stale author results after append or deletion, contention with manual compaction, failure diagnostics, and shutdown/restart followed by successful retry.

    **Recommendation:** **Keep, prioritizing data-preservation races and restart recovery.** Use real gated authors and database mutations to verify stale append/delete/fork rejection, cancellation followed by restart/retry, and unchanged canonical history. Retire internal plan-shape assertions where existing outcome and configured-limit tests establish the same guarantee.

60. **Transcript tool errors and limits:** Complete tool-level output budgets, fragment continuation, query/title limits, cross-workspace access cases, real full-text-search failure diagnostics and untrusted-operation logging.

    **Recommendation:** **Keep permissions, output limits and actual tool continuation as durable contracts.** Add a compact real-tool boundary matrix for workspace/session access, fragment continuation, query/title bounds and FTS failure correlation. Reuse item 20’s confidentiality proof rather than manufacturing a special backend exception in every retrieval scenario.

61. **Pinned session-map policy:** Verify behavior after changing the global strategy, exact compaction-threshold equality, repeated reductions, unavailable models and distinct failure-warning counts.

    **Recommendation:** **Keep strategy pinning, no-fallback behavior and the chosen threshold boundary; combine with item 53.** Confirm whether equality should trigger reduction, then make that an explicit policy assertion. Retire an exact eight-message fixture requirement unless it protects a specific complete-turn or evidence-boundary invariant.

62. **Old adapter versus current evidence capture:** Review the old requirement to rewrite the application’s original log with redacted values. Current capture preserves original observations and redacts exported evidence; the old adapter self-test still fails that expectation.

    **Recommendation:** **Retire the requirement that the harness rewrite producer logs.** Preserve original observations and redact exported evidence. Separately verify that production code does not emit credentials in the first place, so harness redaction cannot hide an application logging defect.

63. **Old runner isolation policies:** Decide which fresh-process, environment-whitelist, `HOME`/temporary-directory isolation and hard-timeout policies must carry forward. Current pytest workers do not automatically establish those guarantees; this overlaps item 4.

    **Recommendation:** **Merge with item 4 and allow worker reuse only after confirmed cleanup.** Keep isolated data/system roots, explicit test credentials, outbound restrictions, cleanup quarantine and process-level hang protection. Do not restore blanket per-case process/HOME/temp replacement unless a concrete contamination case requires it.
