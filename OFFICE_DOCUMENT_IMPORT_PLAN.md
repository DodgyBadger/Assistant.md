# Word and PowerPoint import

Status: implementation and focused validation complete; approved documentation applied. All documentation wording, including the destination-option correction, is approved and applied. Support vault-file DOCX and PPTX import through the existing durable import pipeline. Preserve the existing capture-images setting; Excel remains a shell workflow. No full-suite run is authorized for this effort.

## Verified provider behavior and scope

Live synthetic DOCX and PPTX probes both succeeded through Mistral's existing inline-base64 transport. The normal PDF image-off request (`include_image_base64=false`) was rejected for both Office formats. Office requests succeed with `include_image_base64=true` and `image_limit=0` when images are unwanted. Reports are under `/tmp/assistantmd-office-ocr-smoke-20261109T215721Z` and `/tmp/assistantmd-office-ocr-smoke-20261109T215748Z`. Neither probe modified application data or submitted private documents.

Implement local vault files, including Explorer upload/import and chat/workflow submission. Keep Office source files and selected destinations intact. Produce Markdown and, when requested, extracted image assets through the existing mutation and output-allocation boundaries. Keep PDF image-on/off behavior unchanged. Public-URL Office ingestion, spreadsheets, legacy DOC/PPT formats, and native Office editing are outside this change.

## Implementation

- Add lightweight binary source loaders for DOCX and PPTX with their actual MIME types; do not add Office parsing libraries to application dependencies.
- Follow ADRs 0011 and 0030: keep source loading separate from extraction and retain format-specific `docx_ocr` and `pptx_ocr` strategies. Share Mistral document transport, options, response parsing, rendering and storage with the existing PDF strategy.
- Select the Office strategy automatically and require `MISTRAL_API_KEY`. Expose supported file extensions through the existing registry-derived metadata used by Explorer. Office strategy availability must not depend on the user's PDF fallback preferences.
- Translate image capture at the provider boundary: PDFs use their existing boolean; Office uses base64 image output with `image_limit=0` when capture is disabled. Still honor the logical capture setting when retaining provider response images.
- Preserve normal job provenance, provider failures, source preservation, collision handling, and asset-link rewriting.

## Validation

Extend the existing OCR/import suite with real application submission and queue processing of synthetic DOCX/PPTX fixtures. Verify import admission, actual request MIME/content bytes, strategy/provider provenance, selected destination, Markdown output, preserved sources, and capture-images on/off asset behavior. Only the external Mistral HTTP response is controlled. Include missing-credential behavior and keep existing PDF scenarios as the regression oracle.

Demonstrate meaningful red before production edits and rerun the same acceptance conditions after implementation. Use focused Office, PDF OCR, direct import, queue and compensation scenarios plus admission checks and the complete production quality gate. No full deterministic or live-service suite rerun is needed. A narrowly scoped live Office adapter probe may verify the final provider request without changing persistent runtime configuration.

## Documentation review

Propose exact updates to `docs/tools/content_import.md`, `docs/use/importing-content.md`, ADR 0030 and v0.9.5 release notes. Obtain explicit approval before changing those protected files. Keep tool guidance lean, describe the current contract, and record any pending approval here.

## Implementation and validation evidence

- Added registry-backed DOCX/PPTX binary loaders and format-specific `docx_ocr`/`pptx_ocr` strategies. Both share the Mistral document adapter with PDF OCR. No application dependencies, settings, UI code, or Excel ingestion were added. Explorer discovers the new file types from existing registry-derived capability metadata.
- The provider request always enables base64 image output for Office files; image capture off additionally sets `image_limit=0`. Response retention still follows the logical capture setting, including ignoring unexpected images when capture is off. PDF request flags remain unchanged.
- Genuine synthetic Office fixtures are retained under `validation/fixtures/files/`. Application scenarios exercise real API submission, job processing, MIME/content serialization, selected destinations, provider/strategy provenance, source preservation, asset persistence, and missing-credential outcomes. Local-only PDF preferences do not disable Office extraction.
- Meaningful red: The initial attempt contained an incorrect settings-request value type; it was corrected before acceptance red was claimed. The valid pre-implementation run rejected Office admission in all six cases, recorded in `validation/runs/pytest_20261109_225338_0ecf8d690d/report.md`.
- Initial green: `validation/runs/pytest_20261109_225455_64d047d5ca/report.md`, all eight OCR scenarios passed. A broader run found the existing exact capability-extension list needed the two newly supported formats; its assertion was updated to the agreed supported list.
- Focused final run: `validation/runs/pytest_20261109_225727_2520df701c/report.md`, all 19 import/OCR/queue/compensation scenarios passed. Explicit PDF image-off wire guards then passed in `validation/runs/pytest_20261109_225936_f7c6721cd3/report.md`. All 34 policy checks pass; inventory has no issues. Ruff, Black and mypy pass for the unchanged final production implementation (302 production files).
- Four live production-adapter probes passed against Mistral using synthetic Word and PowerPoint files containing an embedded diagram: capture off returned zero retained images and capture on returned one for each format. Report: `/tmp/assistantmd-office-adapter-live-20261109T225649Z/report.json`. Credential reads used the configured encrypted store, while all settings and log writes used a disposable runtime root. Persistent application settings were not changed.
- The full suite and browser suite were not run, as requested. Existing Explorer behavior is covered through the registry-backed metadata contract and the real import API; the live probes exercise the production extraction adapter rather than the full import queue.
- The user explicitly approved updates to the tool reference, import guide, ADR 0030 and v0.9.5 release notes; all approved wording is applied. The additional destination-option correction is also approved and applied: `destination` defaults to the vault root. No documentation approval remains pending.
