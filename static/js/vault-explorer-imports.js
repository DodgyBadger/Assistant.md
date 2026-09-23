(function vaultExplorerImportsModule(window) {
    const SUPPORTED_EXTENSIONS = new Set([
        '.jpeg', '.jpg', '.pdf', '.png', '.tif', '.tiff', '.webp',
    ]);

    function createVaultExplorerImportsController({ utils, callbacks }) {
        const { escapeHtml } = utils;
        let busy = false;

        function showFiles(overlay, { destination = '', items = [] }, options) {
            showForm(overlay, {
                destination,
                options,
                sources: items.map((item) => item.path),
            });
        }

        function showUrl(overlay, { destination = '', url = '' }, options) {
            showForm(overlay, {
                destination,
                options,
                sources: [],
                url,
                urlMode: true,
            });
        }

        function showForm(overlay, {
            destination,
            options,
            sources,
            url = '',
            urlMode = false,
        }) {
            const panel = overlay.querySelector('[data-vault-explorer-action-panel]');
            if (!(panel instanceof HTMLElement)) return;
            callbacks.closeActionPanel(overlay, { restoreFocus: false });
            panel.innerHTML = `
                <form class="vault-explorer-import-form" data-vault-explorer-import-form
                    data-sources="${escapeHtml(JSON.stringify(sources))}" aria-label="Import to Markdown">
                    <div class="vault-explorer-import-primary">
                        ${urlMode ? `
                            <input name="url" type="url" value="${escapeHtml(url)}" class="vault-explorer-path-input"
                                placeholder="Document URL" aria-label="Document URL" required />
                        ` : `
                            <span class="vault-explorer-import-source-count">
                                ${sources.length} file${sources.length === 1 ? '' : 's'}
                            </span>
                        `}
                        <span class="vault-explorer-import-destination" title="${escapeHtml(destination || 'Vault root')}">
                            <span aria-hidden="true">→</span>
                            <strong class="cell-mono">${escapeHtml(destination || 'Vault root')}</strong>
                        </span>
                    </div>
                    <input type="hidden" name="destination" value="${escapeHtml(destination)}" />
                    <details class="vault-explorer-import-options">
                        <summary>Options <span>Saved defaults</span></summary>
                        <div class="vault-explorer-import-options-grid">
                            <label class="vault-explorer-option-row">
                                <input type="checkbox" name="queue_only" /> Queue only
                            </label>
                            <label><span>PDF output</span>
                                <select name="pdf_mode" class="vault-explorer-path-input">
                                    <option value="">Default</option>
                                    <option value="markdown">Markdown</option>
                                    <option value="page_images">Page images</option>
                                </select>
                            </label>
                            <label><span>PDF strategy</span>
                                <select name="pdf_strategy" class="vault-explorer-path-input">
                                    <option value="">Default</option>
                                    <option value="local_text">Local text</option>
                                    <option value="ocr">Mistral OCR</option>
                                </select>
                            </label>
                            <label><span>OCR images</span>
                                <select name="capture_ocr_images" class="vault-explorer-path-input">
                                    <option value="">Default</option>
                                    <option value="true">Capture</option>
                                    <option value="false">Skip</option>
                                </select>
                            </label>
                        </div>
                        <details class="vault-explorer-import-advanced">
                            <summary>Advanced OCR</summary>
                            <div class="vault-explorer-import-options-grid">
                                <label class="vault-explorer-option-row">
                                    <input type="checkbox" name="include_ocr_blocks" /> Structure blocks
                                </label>
                                <label class="vault-explorer-option-row">
                                    <input type="checkbox" name="extract_ocr_header" /> Separate headers
                                </label>
                                <label class="vault-explorer-option-row">
                                    <input type="checkbox" name="extract_ocr_footer" /> Separate footers
                                </label>
                                <label><span>Tables</span>
                                    <select name="ocr_table_format" class="vault-explorer-path-input">
                                        <option value="">Off</option>
                                        <option value="markdown">Markdown</option>
                                        <option value="html">HTML</option>
                                    </select>
                                </label>
                                <label><span>Confidence</span>
                                    <select name="ocr_confidence" class="vault-explorer-path-input">
                                        <option value="">Off</option>
                                        <option value="page">Page</option>
                                        <option value="word">Word</option>
                                    </select>
                                </label>
                            </div>
                        </details>
                    </details>
                    <div class="vault-explorer-import-footer">
                        <div class="text-sm" data-vault-explorer-form-status></div>
                        <div class="vault-explorer-form-actions">
                            <button type="button" class="ui-button-secondary" data-vault-explorer-action-cancel>Cancel</button>
                            <button type="submit" class="ui-button-primary">Import</button>
                        </div>
                    </div>
                </form>`;
            panel.classList.remove('hidden');
            panel.querySelector(urlMode ? 'input[name="url"]' : 'button[type="submit"]')?.focus();
            panel.dataset.importOptionsAvailable = typeof options.onImportSources === 'function'
                ? 'true'
                : 'false';
        }

        async function submit(overlay, form, options) {
            if (busy || typeof options.onImportSources !== 'function') return;
            const status = form.querySelector('[data-vault-explorer-form-status]');
            const submitButton = form.querySelector('button[type="submit"]');
            let sources = JSON.parse(form.dataset.sources || '[]');
            const urlInput = form.elements.namedItem('url');
            if (urlInput instanceof HTMLInputElement) sources = [urlInput.value.trim()];
            const destinationInput = form.elements.namedItem('destination');
            const queueInput = form.elements.namedItem('queue_only');
            const pdfModeInput = form.elements.namedItem('pdf_mode');
            const pdfStrategyInput = form.elements.namedItem('pdf_strategy');
            const captureInput = form.elements.namedItem('capture_ocr_images');
            const includeBlocksInput = form.elements.namedItem('include_ocr_blocks');
            const extractHeaderInput = form.elements.namedItem('extract_ocr_header');
            const extractFooterInput = form.elements.namedItem('extract_ocr_footer');
            const tableFormatInput = form.elements.namedItem('ocr_table_format');
            const confidenceInput = form.elements.namedItem('ocr_confidence');
            const payload = {
                sources,
                destination: destinationInput instanceof HTMLInputElement
                    ? destinationInput.value
                    : '',
                queue_only: queueInput instanceof HTMLInputElement && queueInput.checked,
            };
            if (pdfModeInput instanceof HTMLSelectElement && pdfModeInput.value) {
                payload.pdf_mode = pdfModeInput.value;
            }
            if (pdfStrategyInput instanceof HTMLSelectElement && pdfStrategyInput.value) {
                payload.pdf_strategies = pdfStrategyInput.value === 'ocr'
                    ? ['pdf_ocr']
                    : ['pdf_text'];
            }
            if (captureInput instanceof HTMLSelectElement && captureInput.value) {
                payload.capture_ocr_images = captureInput.value === 'true';
            }
            if (includeBlocksInput instanceof HTMLInputElement && includeBlocksInput.checked) {
                payload.include_ocr_blocks = true;
            }
            if (extractHeaderInput instanceof HTMLInputElement && extractHeaderInput.checked) {
                payload.extract_ocr_header = true;
            }
            if (extractFooterInput instanceof HTMLInputElement && extractFooterInput.checked) {
                payload.extract_ocr_footer = true;
            }
            if (tableFormatInput instanceof HTMLSelectElement && tableFormatInput.value) {
                payload.ocr_table_format = tableFormatInput.value;
            }
            if (confidenceInput instanceof HTMLSelectElement && confidenceInput.value) {
                payload.ocr_confidence = confidenceInput.value;
            }
            busy = true;
            callbacks.syncInteractionLocks();
            if (submitButton instanceof HTMLButtonElement) submitButton.disabled = true;
            if (status) status.textContent = payload.queue_only ? 'Queueing import…' : 'Importing…';
            try {
                const result = await options.onImportSources(payload);
                const jobs = Array.isArray(result?.jobs_created) ? result.jobs_created : [];
                renderResult(status, jobs, payload.queue_only);
                await callbacks.refreshExplorer(overlay, options, firstOutput(jobs));
            } catch (error) {
                if (status) status.innerHTML = `<span class="state-error">${escapeHtml(error.message)}</span>`;
                if (submitButton instanceof HTMLButtonElement) submitButton.disabled = false;
            } finally {
                busy = false;
                callbacks.syncInteractionLocks();
            }
        }

        function renderResult(status, jobs, queueOnly) {
            if (!status) return;
            const failures = jobs.filter((job) => job.status === 'failed');
            const outputs = jobs.flatMap((job) => job.outputs || []);
            const summary = queueOnly
                ? `${jobs.length} import job${jobs.length === 1 ? '' : 's'} queued.`
                : `${jobs.length - failures.length} import${jobs.length - failures.length === 1 ? '' : 's'} completed.`;
            status.innerHTML = `
                <div class="${failures.length ? 'state-warning' : 'state-success'}">${escapeHtml(summary)}</div>
                ${outputs.map((path) => `<div class="cell-mono">${escapeHtml(path)}</div>`).join('')}
                ${failures.map((job) => `<div class="state-error">${escapeHtml(job.error || 'Import failed.')}</div>`).join('')}
            `;
        }

        function firstOutput(jobs) {
            return jobs.flatMap((job) => job.outputs || [])[0] || '';
        }

        function isBusy() {
            return busy;
        }

        function reset() {
            busy = false;
        }

        return Object.freeze({ isBusy, reset, showFiles, showUrl, submit });
    }

    function supportsPath(path) {
        const name = String(path || '').toLowerCase();
        const dot = name.lastIndexOf('.');
        return dot >= 0 && SUPPORTED_EXTENSIONS.has(name.slice(dot));
    }

    window.VaultExplorerImports = Object.freeze({
        create: createVaultExplorerImportsController,
        supportsPath,
    });
})(window);
