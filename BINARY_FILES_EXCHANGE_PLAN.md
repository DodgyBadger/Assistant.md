# Binary File Operations and Content Capabilities Plan

Branch: `dev/tool-upgrades`. Status: scope agreed; final implementation details remain to settle. Support unrestricted byte-preserving file handling and UTF-8 plain-text read/edit operations, with format-specific ingestion limited by implemented handlers. Implementation has not started. Branch issues: [TOOL_UPGRADES_PLAN.md](TOOL_UPGRADES_PLAN.md). Conversation decisions take precedence over the original PDF-only TODO.

## Agreed scope

Let agents manage source documents and shell outputs without requiring manual file transfers. File copying, moving, deletion, and attachment downloading accept any file type without an extension allowlist. Direct text operations support genuine UTF-8 plain-text files regardless of extension. Import remains limited to implemented ingestion strategies. Do not introduce a file-type permission registry or allowlist setting.

Markdown remains the working representation for notes and context. Supporting transport of DOCX, PDF, or any other file does not imply native content support, automatic extraction, or automatic inclusion in Markdown context. A central capability declaration may simplify existing classification, but do not build a permission registry or new framework without a concrete implementation need.

## Settled decisions

- File-tool calls use explicit filenames and extensions, with no `.md` inference or Markdown-sibling preference. Directory names do not require extensions. Preserve app-level Markdown link conveniences outside generic file-tool resolution. Handling of actual extensionless files remains an edge case to settle; do not rename files implicitly.
- Direct reads of unsupported binary content return a simple unsupported-content error. No shell guidance, automatic extraction, or assumption that the optional advanced shell exists. Preserve existing Markdown and image content handling.
- File tools use vault-relative paths; shell commands use shell-side paths. No shortcuts, aliases, or automatic path translation.
- Exchange is a deployment convention. Operators may mount a narrow folder or the whole vault. No Exchange-location setting, special staging exception, or mount discovery is needed. App tools do not impose a disk-wide invariant on files written directly by users or the shell.
- Deletion is independent of file type, including recursive folder deletion containing arbitrary files. Preserve existing root/identity protection, selection validation, approval, and recovery.
- Permission to transport a file is separate from native reading/editing and ingestion strategies. A DOCX may be stored or moved when no DOCX importer exists. Installing software in the shell does not install a built-in reader or ingestion strategy.

## Source findings and affected surfaces

- `core/vault_state/file_operations.py` and `core/vault_state/file_mutations.py` already move arbitrary file bytes through recorded mutations. Native `copy` is absent. Establish green move baselines rather than treating moves as missing functionality.
- Some reads and edits in `file_operations.py` prefer an existing `.md` sibling for extensionless inputs. `core/utils/patterns.py` has separate Markdown-specific discovery behavior. Audit these consumers against the agreed exact-filename contract.
- `core/constants.py:SUPPORTED_READ_FILE_TYPES` describes Markdown/image content handling and is consumed by discovery, image references, and context construction. Do not expand it into a permission list for byte transport or accidentally route arbitrary binary files into text processing.
- `core/tools/gmail.py` enforces `.pdf` destinations. `core/integrations/google/gmail_service.py` checks a PDF signature and constructs PDF metadata. Remove PDF-only transfer assumptions while preserving authorization, size limits, filename handling, and recorded writes.
- `core/ingestion/service.py` selects actual importer/extractor registrations and already rejects unsupported sources. Preserve that capability boundary independently of file transport.
- The shell's `/exchange` may be a mount of a vault folder such as `AssistantMD/shell-exchange`. The same files are addressed through each tool's own filesystem root; no special transfer protocol is required.
- Relevant decisions: ADR 0023 (file tools/review), ADR 0025 (mutations/recovery), ADR 0042 (advanced-shell boundary).

## Agreed plain-text support

- Broaden direct text operations to genuine plain-text files, initially UTF-8: for example TXT, CSV, JSON, YAML, source files, and Markdown. Do not require a text-extension allowlist. Preserve binary rejection rather than assuming every decodable document is plain text. Detailed detection boundaries remain to be settled before implementation.
- Keep Markdown rendering, frontmatter, note discovery, and routine context construction Markdown-specific. Generic text access must not automatically add every text file to note context. Reading or editing JSON/YAML/CSV means literal text operations, not schema validation or format-aware structured editing.
- Preserve image reads and the agreed simple unsupported-content error for formats without native reading support. No automatic Office extraction or general encoding-conversion subsystem is proposed.
- Source correction: `_read_existing_text_file` already attempts UTF-8 reads for non-image files beyond Markdown; the main functional expansion is removing Markdown-only text-edit restrictions and making the text/binary boundary consistent. Record a real baseline for existing non-Markdown text reads before claiming that behavior is new.

## Remaining decisions before implementation

1. Define consistent text/binary validation for the agreed UTF-8 read/edit contract, including binary files whose bytes happen to decode. Reuse shared validation and preserve unsupported-content errors; no text-extension allowlist.
2. Settle native copy scope, especially directory copying, overwrite handling, and review expectations, without expanding into unrelated file-management features.
3. Settle actual extensionless files under exact path semantics and audit existing executable consumers of Markdown shorthand.
4. Identify review-payload and persistence impacts. No file-type permission settings or settings migration are proposed. Keep existing attachment size settings.

## Acceptance and validation

- Establish existing UTF-8 read baselines, then add real bound-tool create/append/edit/replace journeys for non-Markdown plain text. Expected meaningful red: Markdown-only mutation restrictions. Prove that binary inputs are rejected before mutation and ordinary text files are not implicitly added to Markdown context. Preserve exact filename, content, and relevant line-ending assertions.
- Establish real bound-tool baselines for byte-preserving binary moves, Markdown reads/edits, images, and the current shorthand behavior. Change shorthand expectations only against the explicitly revised contract and preserve meaningful content assertions.
- Add a native copy journey through production tool binding. Compare complete bytes, source preservation, overwrite behavior, recorded recovery, and applicable inline-edit review. Expected meaningful red: absent native copy support.
- Extend Gmail attachment scenarios with the existing local HTTP provider boundary. Download DOCX/PPTX/XLSX and an unlisted format such as ODT, preserve exact bytes, and verify actual filenames/types, size limits, collision handling, and recorded writes. Expected meaningful red: PDF-only transfer restrictions. Keep application services real.
- Verify unknown extensions can be copied, moved, downloaded, listed, and deleted while direct unsupported content reads and ingestion still return their respective errors. Neither operation support nor error text should depend on advanced-shell availability or a folder being called Exchange.
- Exercise exact filenames with `report`, `report.md`, and `report.docx` together, proving no sibling selection or inferred filenames. Directory operations must accept directory names without extensions. Settle the actual extensionless-file expectation before encoding it.
- Verify vault-relative exchange placement through real file operations. Claim a shell round trip only when an isolated deterministic fixture exercises an actual shared mount; otherwise report that deployment boundary as untested.
- Preserve traversal rejection, read-only mounts, vault-root/identity protection, overwrite safeguards, standard approval/script policy, and recovery. Do not add binary content to routine Markdown context or text-edit paths.

## Implementation sequence

1. Settle the remaining text-validation, copy-scope, and exact-path edge cases without reopening the agreed unrestricted transport and broad text support.
2. Establish working baselines and add targeted failing scenarios for native copy, non-Markdown edits, PDF-only Gmail restrictions, and removed Markdown shorthand.
3. Implement byte-preserving copy through recorded mutations, generalize attachment transfer, and share text validation across read/write/append/edit/replace. Keep ingestion handler selection and Markdown-specific context behavior independent.
4. Verify approval, overwrite, recovery, exact bytes, text integrity, and unsupported binary content through real production entry points. Run the required quality gate and stable-branch core profile.
5. Review and apply explicitly approved documentation updates as specified below.

## Execution and delivery

Use isolated scenario runtime roots, never repository `data/` or `system/` as scratch storage. Apply behavioral planning and scenario skills. Record genuine red/green evidence and limits here during implementation. The user removed earlier run artifacts; historical report references elsewhere on the branch are not retained artifacts.

Once decisions are settled, implement the failing acceptance scenario first, confirm missing behavior rather than setup failure, implement the feature, and rerun the same oracle. Use targeted checks during development, the full production Python quality gate, and the full deterministic core profile during hardening. Experimental and live-service scenarios remain opt-in.

Finish with documentation review: identify affected file-tool, Gmail, shell/exchange, and architecture documents; present exact proposed text or diffs; obtain explicit user approval; then apply approved updates. No protected documentation changes are authorized by this planning discussion.

Next phase: settle remaining implementation details for text validation, native copy, and exact filenames, then move to Feature Development using the acceptance scenarios above. No production code or runtime settings have changed.
