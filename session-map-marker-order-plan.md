# Session map marker ordering during a live turn

Keep the session map boundary between represented canonical history and new live messages while the model response is running. The marker conveys a context boundary, so moving an unrepresented user request or live response above it incorrectly suggests that those messages are already represented by the map.

## Findings and implementation

`static/js/chat-history-rendering.js:positionTimelineCheckpointNotice` searches for the first canonical row after the checkpoint. Newly submitted user messages and streaming assistant rows do not yet have canonical sequence attributes. If there is no later canonical row, compaction-status refresh appends the marker to the end, moving it past both live rows. The persisted-history reload after completion supplies canonical sequence attributes and corrects the position.

Anchor the notice after the last represented canonical row when there is no later canonical row. Preserve an already-positioned notice if no canonical anchor is available. Keep first-unrepresented-row placement and failure notice ordering intact. Do not assign invented sequence numbers to live rows or change compaction persistence.

## Validation

Add one real browser case to the existing session browser scenarios. Seed a native session map ending at the latest persisted message, submit a new user request through the UI, hold only the external model response, and allow the real compaction-status refresh to complete. Assert that the map boundary precedes the user request and active response before completion, then retain the order after completion. DOM relative order is the explicit chronology/context-boundary contract here; do not assert marker copy, styling or a screenshot snapshot. Retain meaningful red/green evidence, register the scenario source hash and identity, and run the relevant existing timeline-pagination case. No full suite is requested.

## Results

The regression failed before the fix because the map marker followed the new user request while the external model was held: `validation/runs/pytest_20261103_185252_6accc99788/report.md`. The same case and existing older-timeline pagination scenario passed afterward: `validation/runs/pytest_20261103_185406_d2f5f3019d/report.md`. Lifecycle artifacts confirm inactive runtime and successful controller cleanup. JavaScript syntax, scenario Ruff/Black, canonical inventory and diff whitespace checks passed. No full suite was run.

## Documentation review

No protected documentation changes are needed for this correction. Present any later documentation draft and obtain explicit approval before editing protected files.
