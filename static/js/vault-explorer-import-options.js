(function vaultExplorerImportOptionsModule(window) {
    function renderMarkup(extraContent = '') {
        return `
            <details class="vault-explorer-import-options">
                <summary>Options <span data-vault-explorer-import-options-summary>Saved defaults</span></summary>
                ${extraContent}
                <div class="vault-explorer-import-options-grid">
                    <label class="vault-explorer-option-row"><input type="checkbox" name="queue_only" /> Queue only</label>
                    <label><span>PDF output</span>
                        <select name="pdf_mode" class="vault-explorer-path-input">
                            <option value="">Default</option><option value="markdown">Markdown</option><option value="page_images">Page images</option>
                        </select>
                    </label>
                    <label><span>PDF strategy</span>
                        <select name="pdf_strategy" class="vault-explorer-path-input">
                            <option value="">Default</option><option value="local_text">Local text</option><option value="ocr">Mistral OCR</option>
                        </select>
                    </label>
                    <label><span>OCR images</span>
                        <select name="capture_ocr_images" class="vault-explorer-path-input">
                            <option value="">Default</option><option value="true">Capture</option><option value="false">Skip</option>
                        </select>
                    </label>
                </div>
                <details class="vault-explorer-import-advanced">
                    <summary>Advanced OCR</summary>
                    <div class="vault-explorer-import-options-grid">
                        <label class="vault-explorer-option-row"><input type="checkbox" name="include_ocr_blocks" /> Structure blocks</label>
                        <label class="vault-explorer-option-row"><input type="checkbox" name="extract_ocr_header" /> Separate headers</label>
                        <label class="vault-explorer-option-row"><input type="checkbox" name="extract_ocr_footer" /> Separate footers</label>
                        <label><span>Tables</span>
                            <select name="ocr_table_format" class="vault-explorer-path-input">
                                <option value="">Off</option><option value="markdown">Markdown</option><option value="html">HTML</option>
                            </select>
                        </label>
                        <label><span>Confidence</span>
                            <select name="ocr_confidence" class="vault-explorer-path-input">
                                <option value="">Off</option><option value="page">Page</option><option value="word">Word</option>
                            </select>
                        </label>
                    </div>
                </details>
                <button type="button" class="ui-button-secondary" data-vault-explorer-import-reset-options>Reset to current defaults</button>
            </details>`;
    }

    function updateSummary(form) {
        const summary = form.querySelector('[data-vault-explorer-import-options-summary]');
        if (!summary) return;
        const overridden = Array.from(form.querySelectorAll(
            'select[name], input[type="checkbox"][name]'
        )).some((control) => (
            control instanceof HTMLSelectElement
                ? Boolean(control.value)
                : control instanceof HTMLInputElement && control.checked
        ));
        summary.textContent = overridden ? 'Overrides active' : 'Saved defaults';
    }

    function reset(form) {
        form.dataset.importRequestOptions = '{}';
        form.dataset.importTouchedOptions = '[]';
        form.querySelectorAll('select[name], input[type="checkbox"][name]')
            .forEach((control) => {
                if (control instanceof HTMLSelectElement) control.value = '';
                if (control instanceof HTMLInputElement) control.checked = false;
            });
        updateSummary(form);
    }

    function bind(form) {
        const handleChange = (event) => {
            const name = event.target?.name;
            if (name) {
                const touched = new Set(JSON.parse(form.dataset.importTouchedOptions || '[]'));
                touched.add(name);
                form.dataset.importTouchedOptions = JSON.stringify(Array.from(touched));
            }
            updateSummary(form);
        };
        form.addEventListener('input', handleChange);
        form.addEventListener('change', handleChange);
        form.querySelector('[data-vault-explorer-import-reset-options]')
            ?.addEventListener('click', () => reset(form));
        updateSummary(form);
    }

    function read(form) {
        const value = (name) => form.elements.namedItem(name);
        const touched = new Set(JSON.parse(form.dataset.importTouchedOptions || '[]'));
        let payload = {};
        try {
            const preserved = JSON.parse(form.dataset.importRequestOptions || '{}');
            if (preserved && typeof preserved === 'object' && !Array.isArray(preserved)) {
                payload = { ...preserved };
            }
        } catch (_) {
            payload = {};
        }
        const queue = value('queue_only');
        payload.queue_only = queue instanceof HTMLInputElement && queue.checked;
        const pdfMode = value('pdf_mode');
        if (touched.has('pdf_mode')) {
            delete payload.pdf_mode;
            if (pdfMode instanceof HTMLSelectElement && pdfMode.value) payload.pdf_mode = pdfMode.value;
        }
        const pdfStrategy = value('pdf_strategy');
        if (touched.has('pdf_strategy')) {
            delete payload.strategies;
            delete payload.pdf_strategies;
            if (pdfStrategy instanceof HTMLSelectElement && pdfStrategy.value) {
                payload.pdf_strategies = pdfStrategy.value === 'ocr' ? ['pdf_ocr'] : ['pdf_text'];
            }
        }
        const capture = value('capture_ocr_images');
        if (touched.has('capture_ocr_images')) {
            delete payload.capture_ocr_images;
            if (capture instanceof HTMLSelectElement && capture.value) {
                payload.capture_ocr_images = capture.value === 'true';
            }
        }
        for (const name of ['include_ocr_blocks', 'extract_ocr_header', 'extract_ocr_footer']) {
            const input = value(name);
            if (touched.has(name) && input instanceof HTMLInputElement) {
                payload[name] = input.checked;
            }
        }
        for (const name of ['ocr_table_format', 'ocr_confidence']) {
            const input = value(name);
            if (touched.has(name)) {
                delete payload[name];
                if (input instanceof HTMLSelectElement && input.value) payload[name] = input.value;
            }
        }
        return payload;
    }

    function preserve(form, requestOptions = {}) {
        const preserved = {};
        for (const name of [
            'capture_ocr_images',
            'clean_html',
            'extract_ocr_footer',
            'extract_ocr_header',
            'include_ocr_blocks',
            'ocr_confidence',
            'ocr_table_format',
            'pdf_mode',
            'pdf_strategies',
            'strategies',
        ]) {
            if (requestOptions[name] != null) preserved[name] = requestOptions[name];
        }
        form.dataset.importRequestOptions = JSON.stringify(preserved);
        form.dataset.importTouchedOptions = '[]';
    }

    window.VaultExplorerImportOptions = Object.freeze({ bind, preserve, read, renderMarkup });
})(window);
