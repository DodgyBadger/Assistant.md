# Mobile chat excerpt copying assessment

Status: simple paragraph-range approach implemented and validated. The user confirms the original problem occurs on finished responses, never while streaming.

## Findings

Chat message bodies use browser-native selection. The chat modules contain no select-all selection handler or chat-body `user-select: all` rule. The custom selection listener in `static/js/vault-selection-prompt.js` belongs to the vault reader, not chat. Streaming replaces rendered response HTML, but the confirmed finished-response reproduction excludes that path as the explanation here. A specific Chrome/Android bug has not been reproduced on a physical device.

Each message already has a Copy button using its complete source text. Fenced code/plain-text blocks also have individual Copy buttons. These provide immediate copy-ready output when the agent can put the desired content in a separate block, but do not solve arbitrary multi-screen excerpts from an existing response.

The CSS containment option `user-select: contain` is unsupported in current browsers according to [MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/user-select). No reliable one-line containment fix was identified. Do not add speculative selection clamping or alter native selection while the cause remains unverified.

## Accepted simple component

Offer a frozen, message-scoped excerpt dialog from a long press on its existing Copy button. Alt+ArrowDown on that button provides keyboard access. A normal tap continues to copy the complete original message. Choose a starting paragraph and an ending paragraph using taps, with normal scrolling between them. Keep that range in application state and copy it through the existing clipboard utility. Clearly expose the selected range; cancellation leaves the conversation unchanged. This avoids requiring native selection handles to cross the chat scroll viewport and works with ordinary responses without a new agent tool or special output protocol.

The dialog shows top-level rendered content blocks as selectable paragraphs, in original order. A list, code block, table or quotation is one selectable block. Copy the visible plain text of the inclusive range, separated by blank lines, without product controls or Markdown formatting syntax. After one tap a single block is copyable; the second tap chooses the end, including a reverse selection. A third tap starts a new range. Copy closes on success; failure leaves the range available to retry. Cancel/Escape before Copy leaves the clipboard unchanged and restores focus to the original Copy button if it still exists. There is no trim/edit stage, format selector, agent tool or persisted state.

## Implementation and validation

1. Add a small `static/js/chat-excerpts.js` controller that snapshots finished message content and owns the dialog, range, copying and gesture handling. Use the existing clipboard utility. Restore focus, trap dialog keyboard focus, and keep normal scrolling available between taps.
2. Attach it only to full-message Copy controls in `static/js/chat-message-controls.js` and `static/js/chat-rendering.js`; code-block and tool-detail copy buttons retain their behavior. Load the controller before message controls in `static/index.html`, with styling in `static/app.css`.
3. First add real-app browser scenarios using native persisted conversation inputs and actual clipboard permissions. Meaningful red: a long press copies the whole response instead of opening excerpt selection. Green must prove inclusive range content, normal full copy, release-click suppression, scroll-spanning range persistence, single/reversed selection, cancellation, keyboard access, and unchanged conversation/draft state. Exact clipboard content is the functional output contract. Do not add presentation assertions or snapshot oracles.
4. Register canonical scenario source hashes and identities. Run focused new cases and the existing persisted-message clipboard case, JavaScript syntax checks, scenario lint/format and inventory checks. No full suite. Mobile emulation establishes application interactions, not physical Android native handle behavior. This component bypasses those handles rather than claiming to repair the browser.


## Validation results

The first real-browser acceptance failed because long press had no excerpt dialog: `validation/runs/pytest_20261103_191559_34917f5e14/report.md`. The final run passed three excerpt cases (including desktop/mobile range scrolling and keyboard/single/reversed/cancel behavior) and the existing persisted Markdown/clipboard case: `validation/runs/pytest_20261103_192046_92ac15acfe/report.md`. Native clipboard contents were asserted with browser permissions; application collaborators and clipboard methods were not mocked. Lifecycle artifacts confirm inactive runtime and controller cleanup. Diagnostic `excerpt-range.png` images supported direct visual review and are not snapshot assertions.

JavaScript syntax checks, scenario Ruff/Black, canonical source inventory and whitespace checks passed. No production Python or backend contract changed, and no full suite was run. Physical Android device usability remains for direct user confirmation; the application workflow bypasses native selection handles.

## Documentation review

No protected documentation edits were made during this implementation. Present any future affected documentation and exact draft for explicit approval before editing protected files.
