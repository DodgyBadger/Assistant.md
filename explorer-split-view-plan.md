# Desktop explorer split view

Implement the agreed desktop toggle beside Close, a draggable vertical divider, and browser-local persistence of the last mode and split ratio. Start at 60% chat / 40% explorer. Require at least 1100px viewport width, 480px for chat and 360px for explorer. At narrow widths use the existing modal presentation while retaining the desktop preference.

## Implementation

- Add one shared frontend layout controller, using the existing failure-safe browser storage boundary. Register both the tree and file reader as surfaces; update presentation in place without recreating editors or navigation state.
- Reserve a right-hand column for docked surfaces and shrink the chat shell accordingly. Apply the same reserved space to the fixed focus-mode chat surface. Keep dock z-order below full-screen dialogs; only the top reader covers the tree within the dock.
- Remove modal backdrop interaction, modal accessibility state and the tree Tab trap while docked. Outside clicks can interact with chat. Escape closes the dock only when focus is inside it, respecting nested menus, action panels, uploads and unsaved-edit guards.
- Add pointer capture and keyboard Left/Right/Home/End support to the vertical separator. Persist the ratio after adjustments and the mode after toggling. Closing/reopening or page reload restores both. Narrow-screen fallback must not overwrite either saved preference.
- Keep existing active-response editing/mutation locks. Close docked explorer/reader surfaces before vault, conversation, new-chat or tab changes; cancel that context switch if the user declines to discard unsaved edits.

## Behavioral validation

Use the installed behavioral-testing skills and real browser/app fixtures. First establish red for the missing dock toggle. Assert simultaneous chat interaction and real file access, preserved editor text when changing presentation, actual resize changes and bounds, mode/ratio restoration after closing and reload, narrow-window fallback/restoration, focus-mode coexistence, active-run editing locks, and guarded vault changes. Geometry checks apply only to the explicit functional resize and non-overlap requirements, not fixed pixel styling. Do not assert theme, copy or decorative markup. Use native persisted files and a controlled loopback model only where a running response is necessary. Register scenario admission hashes and retain red/green/lifecycle evidence. Run focused cases, syntax checks, scenario lint/format and inventory checks; no full suite.

## Documentation review

No protected documentation edits are authorized by this implementation request. If user-facing guidance needs updating, present the exact affected files and proposed draft in chat and obtain explicit approval before editing protected files.

## Delivery evidence

Implemented the shared layout controller, desktop header toggle, pointer/keyboard divider, local mode/ratio persistence, narrow-screen modal fallback, focus-mode sizing and context-switch guards. Both tree and reader use the same preference; switching presentation preserves their DOM and unsaved text. Covered tree surfaces become inert while a reader is open. Normal response completion refreshes the same conversation without closing the dock.

The initial missing-toggle red is retained in `validation/runs/pytest_20261103_194451_95d38ccbb8/report.md`. The first implementation run exposed a hidden-button CSS override and a missing exported close method; both were fixed without weakening those behavioral expectations. Escape isolation and completed-response preservation were added to the acceptance sequence afterward.

Final targeted browser evidence: `validation/runs/pytest_20261103_195013_59609de14d/report.md` — all three new scenarios and two existing explorer/file-edit regression scenarios passed (5 total). Setup, call and teardown passed; no worker crashes, browser page errors or denied requests; browser contexts closed. Retained lifecycle and listener cleanup artifacts establish resource cleanup. Browser screenshot review confirmed the split and themed controls.

Seven changed JavaScript files passed syntax checks. Scenario Ruff/Black checks, CSS compilation and admission inventory passed; the 34 validation/collection policy checks passed. No full suite or live-service validation was run. The controlled provider proves application behavior around a running response, not live-provider compatibility. No protected documentation was edited.
