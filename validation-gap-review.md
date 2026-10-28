1. **Settings conflict responses:** Verify that a real concurrent settings change produces the expected HTTP 409 response. The old test injected a repository conflict.

2. **Secrets migration crash recovery:** Test abrupt process death during import, after import, and after renaming the source. Exception recovery is covered; actual crash recovery at those boundaries is not.

3. **Root-folder deletion policy:** Decide whether deletion using `.` or `./` should be rejected. The rejection requirement came from an evaluation exercise, rather than an established product specification.

4. **Hard timeouts and child-process cleanup:** Decide and verify how hung tests and their descendant processes should be terminated. The new plugin does not currently provide the old runner’s guarantees.

5. **Concurrent writes to the same file:** Successful results and preservation of updates are covered. Actual non-overlap between active writes has not been observed.

6. **Editing during a directory move:** Verify that a child-file edit waits correctly while its directory is being moved. Sequential move and stale-path rejection do not establish this.

7. **Rollback of an artificially reactivated snapshot:** Decide whether recovery from a snapshot that was already rolled back, then manually reactivated with missing mutation history, is a supported corruption-recovery contract.

8. **Concurrent activity rollback:** Verify that only one restore executes while another rollback request arrives. Current tests establish correct public results, but not the original internal restore-count assertion.

9. **Guarded secret-write concurrency:** Verify the precise concurrency boundary between checking an encrypted secret and updating it. The old test paused a private database lookup.

10. **Default context instructions:** Decide which missing-README advice and packaged policy instructions are durable behavior. Their inclusion and structure are covered, but their intended meaning is not independently tested.

11. **Locked bootstrap with cold imports:** Investigate why some cold import orders create an unrelated chat database and migration backup. Existing passing cases use prewarmed modules and do not cover every startup order.

12. **Importing public URLs:** Verify successful remote fetching, destination selection, provenance, PDF detection from content, and extensionless URLs. Local imports and outbound-policy rejection are covered.

13. **Import authority before job creation:** Verify that missing execution authority rejects an immediate import before creating a durable job. The old test injected failure into authority resolution.

14. **Batch-move compensation:** Verify rollback after failures partway through moving files or directories. Preflight rejection and ordinary successful moves do not establish compensation after partial completion.

15. **Interrupted upload cleanup:** Verify that a real write interruption leaves no partial upload behind. Size limits, collisions and preflight checks are covered.

16. **Future managed-model catalog changes:** Verify catalog renames and update collisions against a changed future template. Current catalog migration and conflict handling are covered.

17. **Transient web-provider failures:** Verify real HTTP 503 handling, `Retry-After` values, and removal of private query text from provider errors. Missing-key behavior does not cover these failures.

18. **Session-map instruction wording:** Decide whether old assertions about exact authoring instructions and setting descriptions should become behavioral checks or be retired.

19. **Transcript index migration:** Test a genuinely old database without transcript indexes. Both the original and migrated tests created indexed messages before reinitializing the schema.

20. **Private text in transcript-search errors:** Verify sanitization when a storage exception actually contains private text. Current real SQLite failures produce generic messages, so the stronger confidentiality claim remains unproved.

21. **Google connection transaction races:** Verify concurrent deletion, default replacement, client-identity changes, credential changes and token commits at the original transaction boundaries. Ordinary concurrency tests cover narrower cases.

22. **Google disconnect rollback:** Verify that failed cleanup during disconnect preserves the previous grant and credentials. Other credential-update rollback cases do not establish this branch.

23. **Gmail connection UI behavior:** Review save-before-authorization behavior and missing-compose warnings in the browser. API scope and readiness checks do not establish rendered UI behavior.

24. **Chat replay, expiry and fork failures:** Cover unresolved-tool fork suppression, projection rollback, cursor-expiry races, expired raw events, missing terminal retention, and reasoning/tool projections through the actual API.

25. **Queued chat approval and preflight:** Verify that queued work retains the approval policy accepted at submission, and that cancellation during preflight behaves correctly. Current tests prove narrower queue and model-invocation behavior.

26. **Delegated image delivery:** Observe image bytes reaching the intended model request. A successful delegate response and an available image fixture do not independently prove image consumption.

27. **MCP OAuth guarded transport:** Verify a successful request through the guarded public-address transport. Client configuration and storage checks do not establish the full socket and DNS boundary.

28. **Workflow output-file ownership:** Determine whether a real workflow producer supplies nonempty `output_files`. The original injected them; current real execution paths return an empty list, although storage persistence is covered.

29. **Unexpected chat exceptions:** Verify generic unexpected failures during preparation and streaming, including their diagnostics and recovery markers. Real image-validation and provider errors exercise different branches.

30. **Chat guidance wording:** Review browser descriptions, usage-limit guidance, recovery-card instructions and unavailable-MCP instructions. Structural behavior is covered; exact editorial wording and its intended meaning are not.

31. **Content-import UI behavior:** Test rendered import interactions and warnings when browser work resumes. API and tool tests do not replace the old JavaScript, CSS and source-text checks.

32. **Execution-task race windows:** Cover the exact transitions around starting, completing, failing, reserving tasks, parent termination and concurrency cleanup. Current real workloads establish broader outcomes, not every original interleaving.

33. **Manual retry and legacy SDK errors:** Resolve whether raw OpenAI `APIError` should still classify as `transient_provider`. Real adapter errors normalize differently; transport-interruption recovery is covered.

34. **Automatic retry recovery branches:** Cover stalled streams, slow active tools, diagnostic truncation, checkpoint replay, rollback-and-restart, and retry with rollback disabled. Ordinary partial-stream retry and shared request budgets are covered.

35. **Cancellation during an active tool:** Verify cancellation while a tool itself is still running. Current coverage cancels while the model waits after the mutation has completed.

36. **Preventing live models in core tests:** Complete the model-call audit and choose enforcement that prevents accidental external inference. Current tests use deterministic models or local providers, but there is no universal fail-closed guard.

37. **Gmail account-specific token routing:** Verify that explicitly selecting each Gmail account sends that account’s token. Current transport fixtures use the saved default grant; selected-account metadata alone is insufficient.

38. **Mixed and binary web extraction:** Verify preservation of successful items alongside rejected URLs, and rejection of binary PDF responses through the actual extraction path. Policy rejection and low-level transport are covered separately.

39. **MCP cleanup during failed bootstrap:** Verify that the real MCP manager starts and shuts down exactly once when startup fails. Successful repair and restart do not establish that exact lifecycle count.

40. **Interrupted summary-data retirement:** Verify atomic rollback if migration fails after dropping tables but before recording completion, followed by successful retry. Successful retirement and dependency refusals are covered.

41. **Automatic compaction admission failures:** Cover failures before compaction enters its normal domain logic, task-admission rejection, and missing or stopped runtime state.

42. **Interruption during an approved tool:** Verify cancellation or failure while a reviewed tool is active, settlement of missing replies, and prevention of replay on the next turn. Current coverage fails after the approved write completes.

43. **MCP mutation race windows:** Cover the original pre-lock credential and OAuth interleavings, plus process death inside the composed credential mutation before commit. Sequential stale-state checks and broader transaction crash tests are covered.

44. **Successful advanced-shell MCP execution:** Verify actual SSH launch configuration, root forwarding and client closure. Configuration gates and refused-connection behavior pass; successful SSH execution lacks local test infrastructure.

45. **MCP DNS and TLS boundaries:** Cover DNS rebinding, multiple-address retry budgets, cancellation during DNS resolution, and TLS hostname/SNI verification. Numeric-address policy and pinned HTTP sockets are covered.

46. **MCP timeout classification:** Resolve a real mismatch: delayed initialization returns `connection_failed`, while a total-deadline probe returned `http_error`. The old mocked test expects `timeout`.

47. **Google OAuth pending-state races:** Cover superseding authorization immediately before pending-state deletion, disconnect racing with credential replacement, and competing starts around pending-state writes.

48. **MCP OAuth authorization lifecycle:** Test actual discovery and authorization start, live callback rejection and recovery, completion timeout, superseding attempts, live disconnect and failed supersede recovery. Current tests begin with saved pending records.

49. **OpenAI device authorization:** Cover device-code start, polling, authorization and API-key fallback while connected. Manual OAuth completion works, but device endpoints currently point to the remote service.

50. **Remaining API error classifications:** Cover non-status OpenAI errors, overload errors, stream-idle timeouts, connection failures and OpenRouter attribution headers. Real HTTP and SDK status-error classifications are covered.

51. **Session-map author failure classification:** Verify generic private runtime-error sanitization and resolve another real mismatch: repeated invalid citations end in SDK retry exhaustion, whereas the old stub expects `SessionMapProvenanceError`.

52. **Legacy session-map evidence repair:** Cover the complete sequence of rejecting invalid citations without changing the checkpoint, accepting valid historical evidence, and repairing pending evidence across later reductions.

53. **Automatic retained-evidence compaction:** Verify automatic post-turn invocation and task-parent relationships. Explicit compaction proves the retained citation behavior, but not the automatic trigger.

54. **Browser resource admission:** Verify actual successful-session concurrency limits and low-memory refusal when browser work resumes. Call budgets are covered without launching Chromium.

55. **Fork snapshot consistency and hydration:** Verify the precise concurrent source-rewrite boundary during a fork, and decide whether the old prohibitions on loading certain historical rows are required contracts.

56. **Remaining delegate boundaries:** Cover cancellation before attachment, private observer counters, distinct attention issues, several diagnostic/redaction helpers, and synthetic internal timeout classification. Parent completion after a child request limit also needs direct proof.

57. **Manual compaction through tools and failures:** Cover tool-initiated compaction, its parent task and returned result, plus remaining cancellation, failure, activity, no-op and exported-diagnostic branches.

58. **Specialized checkpoint boundaries:** Complete the boundary cases for checkpoint creation and tool-history timeline projection, filtering and paging. Review whether the deliberately injected display-metadata inconsistency should remain a test.

59. **Session-context upgrade races and recovery:** Cover plan limits, fork/start races, stale author results after append or deletion, contention with manual compaction, failure diagnostics, and shutdown/restart followed by successful retry.

60. **Transcript tool errors and limits:** Complete tool-level output budgets, fragment continuation, query/title limits, cross-workspace access cases, real full-text-search failure diagnostics and untrusted-operation logging.

61. **Pinned session-map policy:** Verify behavior after changing the global strategy, exact compaction-threshold equality, repeated reductions, unavailable models and distinct failure-warning counts.

62. **Old adapter versus current evidence capture:** Review the old requirement to rewrite the application’s original log with redacted values. Current capture preserves original observations and redacts exported evidence; the old adapter self-test still fails that expectation.

63. **Old runner isolation policies:** Decide which fresh-process, environment-whitelist, `HOME`/temporary-directory isolation and hard-timeout policies must carry forward. Current pytest workers do not automatically establish those guarantees; this overlaps item 4.
