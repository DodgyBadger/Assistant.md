# New chat panel cleanup

Show the selected model label and thinking level as a compact summary, for example `terra (gpt-5.6-terra) - default`. Remove the panel's duplicate settings button and workspace explanatory paragraph. Keep workspace entry controls and favorite chat cards usable on desktop and mobile.

## Implementation

- Replace the model/thinking grid with a wrapping summary line using the current selector values.
- Remove the duplicate settings markup, event handler, callback, and unused styles.
- Remove the workspace help paragraph and its unused styles.

## Validation

Review the compact summary, removed settings button, and workspace simplification directly. Run JavaScript syntax and diff whitespace checks, then rerun the existing canonical chat favorites browser scenario for real application integration. Do not add UI-text assertions or new scenarios. Do not run the full suite.

No production Python or protected documentation changes are needed. Preserve user-selected model and thinking values; the sample model is illustrative, not hard-coded.
