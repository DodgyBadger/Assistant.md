# Session Browser Names and Contents Search

## Contract

Add a Names/Contents selector to the session browser using the vault explorer search-control styling. Names retains local metadata filtering; Contents invokes the existing authorized lexical discovery service over titles/workspaces, map revisions, and canonical transcripts. Empty queries show the ordinary session list. Show bounded, escaped matching excerpts beneath existing session rows while preserving open, map, upgrade, edit, export, and delete actions. Content results are ranked and capped at 20; the UI states that bound rather than claiming an exhaustive count. No inference, embeddings, index changes, or transcript hydration on ordinary browsing.

## Slices and validation

1. Expose a typed, vault-scoped discovery API with validated query/limit inputs and the existing access boundary. Return canonical session row metadata plus bounded source evidence. Extend the discovery scenario to verify transcript-only and old-map matches, inaccessible-session exclusion, source anchors, invalid queries/limits, and unchanged ordinary listing.
2. Wire the modal search selector with debounced requests, cancellation, and stale-result guards covering query/mode changes, vault switches, closing, and reopening. Reuse existing search-control styles; escape excerpts and visibly distinguish loading, empty, failed, and capped-result states. Extend the controller smoke harness to cover ordering, stale completion, escaping, clearing, and mode changes without inference.
3. Run affected deterministic scenarios, frontend smoke/syntax checks, CSS build, and full Python quality gates. Update user guidance, record results, and commit locally; pushing requires an explicit request. The previous full core run passed 134/134; rerun the full profile during the next broader hardening pass unless this change exposes a shared-contract regression.

## Boundaries and events

The API service adapts discovery results to UI DTOs; the memory service remains the single owner of ranking and source selection. Existing `session_discovery_completed` events report vault identity, candidate/returned counts, and source counts without queries or excerpts. Existing API failure logging handles unexpected failures. Name filtering adds no API calls. Historical map excerpts are candidate evidence, not a replacement for canonical text or an assertion that a past decision is still current.

## Completion

Implemented a typed `/api/chat/sessions/search` endpoint and a native Names/Contents selector using existing vault explorer search-control styling. Contents results retain session actions and display escaped evidence with historical-map labels; searches are debounced and cancelled/invalidated on input, mode, vault, and modal lifecycle changes. Empty input restores ordinary browsing, and capped-result, loading, empty, and error states are explicit.

Discovery, actual chat-tool discovery, and API scenarios passed 3/3 (`20261009_215614_600808`). Seven session-controller/chat-session/selection smoke tests passed, including delayed response rejection, query encoding, vault changes, close/reopen, mode changes, escaped excerpts, errors, and existing upgrade behavior. Node syntax, CSS build, Ruff, Black (495 files), and MyPy (284 files) passed. No browser connection was available for interactive visual verification; a live-build check of desktop/mobile search-control layout remains useful. The full profile was not repeated for this localized endpoint/UI addition; its affected deterministic scenarios passed and the previous full run remains recorded in the discovery plan. No persistent runtime data or configuration was changed by validation.
