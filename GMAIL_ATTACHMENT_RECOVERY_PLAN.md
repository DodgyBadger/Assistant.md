# Gmail attachment download regression

Status: regression corrected and focused validation complete. No full-suite run or production deployment was performed.

## Evidence

Production trace `01a1c1a99a26636683656530eb6ebe23` on November 9 at 18:54–18:55 UTC shows a successful message read with three attachments followed by local download rejection after another successful read of the same message. No attachment download request reached Google. The agent copied the returned token exactly; a script passing a newly returned token directly also failed, ruling out model transcription as the explanation.

The merge base with main is `391ad820d8f767afc1744cbfcde0d2d6d663f820`. Before this branch, the service passed the selected message and attachment IDs directly to Google's attachment endpoint. Commit `6d4a779` introduced a second message fetch and exact token comparison while adding attachment metadata and general file-type support. Existing fixture responses reuse fixed attachment tokens and therefore did not expose this assumption. The unchanged attachment and empty-file scenarios pass as a baseline (`validation/runs/pytest_20261109_190016_2354ba56b3/report.md`).

Google documents `MessagePart.partId` as immutable. Use a compact stable handle based on that part ID to identify the selected attachment within its containing message, then resolve Google's current download token when downloading. Preserve filename, MIME type, declared-size checks, account ownership, byte limits, numbered vault writes, and empty attachments. Do not guess an attachment by filename, size, or position. Keep old raw-token lookup only where it exactly identifies a current attachment; failures should direct the agent to read that message's descriptors again rather than discuss draft mutation.

## Validation and implementation

- Extend the existing attachment suite with a real read/download journey whose controlled HTTP provider changes download tokens between reads while preserving immutable part IDs. Include nested parts and duplicate filenames so selecting by name cannot pass accidentally. Keep application authorization, HTTP client, resource service, tool, vault writes, and mutation recording real. Confirm meaningful red before production edits.
- Keep provider download tokens inside the client/service boundary. Return stable handles from resource reads and use the matching part's current provider token for the download. Avoid cache/session coupling and additional settings.
- Give missing-attachment errors specific recovery guidance while retaining existing message/thread/draft guidance for those operations.
- Run focused Gmail read, download, size, token-account, draft and failure scenarios plus the production quality gate and admission checks. Do not run the full suite.
- Review existing Gmail tool documentation for alignment. Present any proposed edits to protected docs or release notes in chat and obtain explicit approval before applying them. Record any pending wording here.

## Results and limits

- The production agent passed the exact token returned by the earlier read, and its subsequent script forwarded a freshly read descriptor directly. The local preflight check still rejected the attachment before reaching Google's attachment endpoint. Token instability between reads is the explanation supported by these observations; raw provider response bodies for the internal preflight were not instrumented, so no claim is made that those exact raw payloads were captured.
- The provider client now retains immutable message-part identity. Resource reads return compact stable attachment handles; downloads resolve those handles to the selected part's current provider token and retain provider filename, MIME type, size checks, account selection, and byte limits. Tool payloads omit the redundant internal part-ID field. Existing exact raw-token matching remains available for current tokens without guessing by name or size.
- Added a meaningful read/download regression case to the existing attachment suite. It uses nested parts, duplicate filenames, changed provider tokens, actual HTTP, account authorization and real vault writes. Red: `validation/runs/pytest_20261109_190306_a6397b3f9c/report.md`. Green: `validation/runs/pytest_20261109_190403_50b5f731ac/report.md`.
- All 15 focused Gmail scenarios passed: `validation/runs/pytest_20261109_190453_b3b42f6f2f/report.md`. All 34 admission/collection policy checks passed; inventory has no issues. Ruff, Black, mypy for all 300 production files, and diff checks pass. The full suite was not run, as requested.
- Existing `docs/tools/gmail.md` already describes attachment IDs as handles returned by message reads and requires the containing message ID; that contract remains accurate. No protected documentation edits are needed for the internal handle resolution. Specific attachment lookup failures now advise `get_message` rather than unrelated draft operations.
- These scenarios verify application behavior against controlled Gmail HTTP responses, not a fresh download from the user's production mailbox. The user should read the message again after deploying this fix to obtain the stable handles for that conversation.
