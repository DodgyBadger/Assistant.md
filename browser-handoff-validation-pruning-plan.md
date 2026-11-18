# Browser handoff validation pruning recommendation

## Review scope and conclusion

Review baseline: `main...af4f5be`. The branch adds 92 test functions, expanding to 153 cases when the declared parameters and the direct/proxy UI fixture are counted, across 17 scenario files. The pre-existing browser budget test and pre-existing event-buffer test are excluded from these counts. Validation changes, including helpers and admission metadata, add approximately 7,800 lines. These are source-inventory counts, not a new pytest collection or execution result.

There is worthwhile excess. The largest opportunities are repeated full browser/sign-in setup, parameter cross-products that do not represent different contracts, several observation-budget checks, and separate process-stall reproductions for each implementation checkpoint. Most assertions do protect actual behavior; this is mainly duplication and over-expanded execution, rather than a suite full of presentation snapshots.

Recommend approximately 75–85 routine cases, including seven rendered UI cases, plus approximately 10–15 explicitly selected process/fault hardening cases. These are sizing estimates for the proposed consolidations, not acceptance quotas. Do not preserve redundant scenarios simply by marking them slow. Delete duplicates and their exclusive helpers after moving any unique assertions into a named keeper. Do not manufacture one giant scenario to lower the count.

This document recommends changes only. No scenario, parameter, admission entry, helper or execution profile is changed by this review, and no suite was run for it.

## Lasting contracts

1. Open browser, perform a brief private human step, return to the same task/context, and retain the website login. Close viewer keeps human ownership; End destroys the context; a stopped handoff can be recovered or explicitly ended.
2. Principal, chat, task and handoff generation determine authority. Stale controls cannot affect a new browser or resume a stopped task. Supported authentication modes and browser-facing same-origin/CSRF requirements remain enforced behind a proxy.
3. Credentials, private input and pixels do not enter model context, history, ordinary logs or telemetry. Handback refuses populated or uninspectable credential surfaces, including inactive popup/original pages.
4. Agent and human actions use current, single-use references. Unsafe changes reject an action; unrelated page churn must not make a stable form unusable. Writes and uncertain gestures are never replayed automatically.
5. One inactivity clock governs retained authentication. Accepted work refreshes it, polling does not, active work can finish, and expiry/deletion/restart releases resources and invalidates old state.
6. Browser children exclude app secrets and unsafe ambient controls; profiles stay outside persistent roots and are removed only after confirmed cleanup. Public-network restrictions, bounded admission, mandatory sandboxing and uncertain-cleanup refusal remain intact.
7. Ordinary chat must still load and finalize when no browser handoff exists. A failed browser/response must expose useful recovery without retrying a mutation or falsely reporting confirmed cleanup.

## Proposed routine UI set

Reduce the current 12 cases to these seven. Keep the user-visible assertions for focus, scroll stability, reachable Return control, private typing, credential refusal and queued handback in the successful journey; they are behavior, not decorative snapshots.

| Keeper | Boundary protected |
| --- | --- |
| Direct successful handoff | Open, Close/reopen, private login, queued handback, same-task continuation and history reload |
| Proxied successful handoff | Actual browser-owned request headers crossing a real proxy; do not substitute request forwarding that drops Fetch Metadata |
| Direct Stop and reload | Paused session remains discoverable; End releases it without agent continuation |
| Direct explicit End | Ending an active viewer destroys the context rather than returning control |
| Proxied lost End response | The real End succeeds, history replaces its context, delivery fails, and uncertainty is visible without mutation replay |
| Authentication popup | One sign-in window in the same modal, safe page switching, original-page restoration and handback |
| Ordinary persisted chat | Opening and reloading history without any handoff still creates usable message actions |

Remove the two standalone detach variants, the proxied duplicates of Stop and End, and the direct duplicate of lost-response recovery. Preserve queued-intent disposal by detaching the viewer during the retained Stop or End journey before releasing its held frame; retain the assertion that no return is sent. Do not assume Stop and DOM detachment are identical. Give the short recovery cases their own readable setup rather than replaying checkbox focus, password entry, refused handback and every preview assertion before each outcome.

## File-by-file disposition

Counts below are branch-added cases before pruning. A consolidation is conditional on preserving the identified contract in its keeper; an unimplemented proposed merge is not existing coverage.

| File | Added cases | Recommendation |
| --- | ---: | --- |
| `test_browser_handoff_ui.py` | 12 | Keep the seven UI cases above; remove redundant ingress/outcome combinations and repeated full sign-in setup. |
| `test_browser_actions.py` | 26 | Keep native form actions, single-use/stale references, meaningful pre-dispatch mutation negatives and a stable-form churn positive. Combine the three observation size/serialization cases into one bounded malicious-page observation. Put disabled-option refusal into the select journey. Consolidate stale-reference lifecycle variants where a keeper already demonstrates the same retirement boundary. Move the two driver-stall cases into the selected fault set. |
| `test_browser_call_budget.py` | 3 | Keep one shared-capacity/refusal/release case. Retain one shutdown-with-owned-DNS case in hardening, including cancellation of shutdown if that is the chosen stronger variant. The original configurable-budget test predates this branch and stays; do not add a disabled-default test. |
| `test_browser_deadlines.py` | 11 | Keep committed-page retention, uncommitted navigation, and both successful/failed fresh inspection after an uncertain POST. Use one representative navigation-timeout value. Fold private-target rejection into the network-denial journey. Keep document-commit attestation and one unresponsive/cancelled deadline in hardening. Avoid retaining every Stop-at-another-await variant. |
| `test_browser_handoff.py` | 25 | Keep ownership blocking, same-task return, stopped/new-generation fencing, credential surfaces and the supported auth-mode matrix. Use one combined waiting/claimed expiry journey with polling and duplicate claims. Consolidate endpoint auth/origin checks with private transport checks rather than duplicating the whole browser setup. Keep one Stop-during-handback race in hardening. Fold waiting crash/deletion resource assertions into their lifecycle keepers. |
| `test_browser_handoff_chat.py` | 2 | Keep the return/replay/provider-privacy case because it directly exercises event replay beyond a DOM check. Remove its Stop variant after preserving stopped-task/no-provider-continuation checks in the UI recovery keeper. |
| `test_browser_human_transport.py` | 11 | Keep actual telemetry/log exclusion, single-use input, stale coordinates, partial-body admission and uncertain-gesture/no-replay behavior. Fold the duplicate happy credential submission into the chat privacy keeper, polling expiry into the idle keeper, and route-auth checks into the owner/endpoint matrix. Keep one late-capture/cancellation case in hardening. |
| `test_browser_lifecycle.py` | 9 | Keep inactivity/auth loss, accepted-activity refresh, restart/process cleanup and shared-engine crash recovery. Fold shortened settings and operation-finish idle timing into the idle family. Use shared-crash coverage rather than separate one-context/two-context reproductions. Keep one cancellation-during-retirement case in hardening; review whether the cross-chat expiry race warrants the second slot. |
| `test_browser_popup.py` | 10 | Keep blank-then-navigate authentication, extra/nested-window denial, private destination denial, POST no-replay and inactive-original credential blocking. The rendered popup case covers direct popup success. Exercise extra and nested denial in one owned session. Select one delayed popup-admission/initialization failure and one root-close lifetime case for hardening. |
| `test_browser_process_environment.py` | 5 | Keep real child-environment exclusion for both retained and extraction paths. Fold debug-output exclusion into privacy and owned-display environment/reaping into the retained child case, forcing the no-ambient-DISPLAY path there. Keep one stopped extraction cleanup case in hardening. Do not keep exact subprocess argument tuple lengths as product assertions. |
| `test_browser_resource_loading.py` | 6 | Keep one mixed-origin CSS burst that includes shared-host concurrency and multiple hosts, one shared-DNS/no-cache/private-answer case, and one actual-job/caller-cap/cancellation case. Use the queued-deadline shutdown variant as the representative hardening case rather than a separate unqueued duplicate. |
| `test_browser_retained_contexts.py` | 9 | Fold simple login retention into handoff success; keep chat isolation/close, shared extraction capacity and private redirect denial. Combine retained/waiting deletion checks under lifecycle deletion, preserving the distinct single-delete and purge entry points. Select one construction/cancellation deadline and the stalled-proxy-shutdown case for hardening if not already covered by the chosen lifecycle fault. |
| `test_browser_settings.py` | 1 | Remove the standalone settings lifecycle scenario after adding the small browser-specific valid/boundary examples to existing settings coverage and retaining live timeout behavior in lifecycle. Generic settings persistence/reset/restart and non-finite parsing do not need a separate browser subsystem scenario. This is a consolidation of existing assertions, not a new test for the call-limit default. |
| `test_browser_temporary_storage.py` | 12 | Reduce the six profile-location combinations to retained and extraction paths with hostile ambient temp locations, including a symlink destination and checks against both persistent roots. Keep one spawn-failure cleanup case. Keep one real uncertain-cleanup/admission-closed case in hardening; fold the duplicate engine-death reproduction into it only if actual storage preservation remains observed. Slow-removal event-loop responsiveness belongs in targeted resource hardening. |
| `test_browser_termination.py` | 8 | Reduce the 2-by-3 actor/state cross-product to four representative cases: agent/human, agent/stopped, viewer/waiting and viewer/stopped. Preserve old-generation/fresh-browser and sibling-chat isolation assertions. Keep returned-generation End rejection and cleanup despite an explicitly exhausted optional budget. UI active End supplies an additional viewer/human integration check. |
| `test_browser_write_recovery.py` | 2 | Keep both confirmed-write/provider-failure and uncertain-action/provider-failure cases. They protect different replay boundaries and are not replaced by a lower-level stale-token check. |
| `test_chat_task_event_buffer.py` | 1 | Keep the added retired-stream/advisory-update case. It is a small component-boundary scenario protecting a separate replay invariant; remove any repeated message-prose checks elsewhere, not this guard. |

## What should not be pruned as incidental detail

Keep different auth modes and both admitted/denied authority cases. A proxy-success test does not prove trusted-peer or owner-token rejection. The current auth-mode test controls ASGI ingress fields; it does not establish production Caddy/nginx/Uvicorn deployment correctness.

Keep frame/shadow/OTP credential discovery and uninspectable-surface refusal. One visible password field cannot establish those boundaries. Both agent-observation guards and human-input guards need coverage: they are separate dispatch paths.

Keep original/current form-membership and node-identity negatives when consolidating action checks. Do not replace all mutation variants with a single generic changed-DOM case. Keep a positive stable-form/unrelated-churn case so security checks cannot silently make interaction unusable.

Keep actual process-environment inspection, profile placement, bounded DNS ownership and uncertain-cleanup admission. Concurrency, memory admission and cleanup uncertainty are separate from the disabled call budget. Do not replace these with a setting-value assertion or pretend that the existing cases prove production memory/CPU capacity or sandbox deployment.

Keep the ordinary-chat regression. It caught a real null-context bug outside the feature's happy path. This is a particularly valuable small keeper, not an implementation-only test.

## Hardening selection and implementation-only material

Select representatives by distinct consequence: shutdown retains owned DNS; stopped native I/O cannot dispatch late; stopped capture cannot disclose late pixels; uncertain cleanup blocks admission; construction has a bounded owner; engine crash invalidates every affected context; popup admission cannot race; stalled proxy clients do not block shutdown. Combine compatible assertions within those representative cases. Do not retain a separate reproduction for every await location or both sides of every boolean solely because they were useful while developing the fix.

The selected hardening set stays available for browser transport, lifecycle and security changes and for final branch hardening. Its exact execution policy must be explicit when implemented. Moving every removed case to an opt-in folder would only move the maintenance burden; delete the rest after coverage transfer. Existing recorded red/green reports and investigation notes can preserve implementation evidence without executable reproductions for every checkpoint.

## Evidence and limits of this review

Inspected the branch diff, test functions, parameter expansion, substantive assertions, real application/provider/network fixtures, relevant settings coverage and ADR 0010. Recent evidence is particularly informative: the initial nine-case UI baseline passed; the later eleven-case run had ten passes and a proxied fault-injection setup defect; corrected direct/proxy lost-response cases passed afterward. The ordinary-chat case first failed meaningfully and then passed unchanged alongside Stop/reload/End recovery. These reports justify retaining those behavior boundaries, not every surrounding parameter combination.

This is a source and existing-evidence review. It does not establish a new full-suite verdict, measure total runtime, or demonstrate the final reduced suite's sensitivity. Existing production proxy/peer/cookie, container sandbox and resource-hardening work remains open. Do not remove a distinct untested release condition because synthetic cases are numerous.

## Implementation sequence and validation

1. Agree the keeper map, then prune one family at a time. Start with the UI matrix, observation-budget overlap and temporary-profile location matrix; they have clear repetition and tangible setup savings.
2. Transfer only unique assertions before deleting a duplicate. Preserve real app/native I/O boundaries, registered synthetic-secret handling and privacy-safe evidence. Simplify shared setup only when it makes the contract easier to read; avoid a generic scenario framework with many mode flags.
3. Select the bounded hardening set explicitly, remove redundant stall variants, and delete helpers/imports/provider branches used only by removed cases. Update exact admission hashes and test identities. Do not leave skipped cases, dead fixtures or orphaned registry entries.
4. Run affected keepers and the admission/collection policies after each family. Reuse relevant genuine failure evidence and review whether each keeper still rejects the demonstrated wrong behavior; do not manufacture a new red for every consolidation or weaken an oracle to obtain green.
5. At the stable branch hardening checkpoint, run the full deterministic core profile, the seven selected rendered cases and the selected fault set. Record setup/call/teardown verdicts and actual private-context/process cleanup. Report before/after case counts and measured runtime then, rather than claiming an estimated speedup now.
6. Review final coverage by the seven lasting contracts above and account for every removed scenario and exclusive helper. Production behavior and saved user settings must not change during this pruning.
7. Documentation review: no protected documentation changes are proposed or made here. If implementing the profile split requires changes to `docs/agent-guides/testing-and-validation.md` or other protected files, present those exact drafts and obtain explicit approval before editing them. The root implementation plans can record the approved pruning and execution evidence.
