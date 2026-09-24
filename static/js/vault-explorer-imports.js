(function vaultExplorerImportsModule(window) {
    function supportedExtensions() {
        const features = window.App?.metadata?.ingestion_capabilities
            ?.file_import?.features;
        return new Set(Array.isArray(features) ? features.map(String) : []);
    }

    function createVaultExplorerImportsController({ utils, callbacks }) {
        const { escapeHtml } = utils;
        const importOptions = window.VaultExplorerImportOptions;
        let busy = false;
        let pollGeneration = 0;

        function showFiles(overlay, {
            destination = '',
            items = [],
            requestOptions = {},
        }, options) {
            showForm(overlay, {
                destination,
                options,
                requestOptions,
                sources: items.map((item) => item.path),
            });
        }

        function showUrl(overlay, {
            destination = '',
            requestOptions = {},
            url = '',
        }, options) {
            showForm(overlay, {
                destination,
                options,
                requestOptions,
                sources: [],
                url,
                urlMode: true,
            });
        }

        function showForm(overlay, {
            destination,
            options,
            requestOptions,
            sources,
            url = '',
            urlMode = false,
        }) {
            const panel = overlay.querySelector('[data-vault-explorer-action-panel]');
            if (!(panel instanceof HTMLElement)) return;
            pollGeneration += 1;
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
                            <strong class="cell-mono" data-vault-explorer-import-destination>${escapeHtml(destination || 'Vault root')}</strong>
                        </span>
                        <button type="button" class="ui-button-secondary" data-vault-explorer-import-change-destination>Change</button>
                    </div>
                    <input type="hidden" name="destination" value="${escapeHtml(destination)}" />
                    ${importOptions.renderMarkup()}
                    <div class="vault-explorer-import-footer">
                        <div class="text-sm" data-vault-explorer-form-status></div>
                        <div class="vault-explorer-form-actions">
                            <button type="button" class="ui-button-secondary" data-vault-explorer-action-cancel>Cancel</button>
                            <button type="submit" class="ui-button-primary">Import</button>
                        </div>
                    </div>
                </form>`;
            panel.classList.remove('hidden');
            const form = panel.querySelector('[data-vault-explorer-import-form]');
            if (form instanceof HTMLFormElement) {
                applyRequestOptions(form, requestOptions);
                importOptions.bind(form);
                form.querySelector('[data-vault-explorer-import-change-destination]')
                    ?.addEventListener('click', () => callbacks.beginDestinationMode?.(overlay, {
                        initialPath: form.elements.namedItem('destination')?.value || '',
                        purpose: 'import',
                    }));
            }
            panel.querySelector(urlMode ? 'input[name="url"]' : 'button[type="submit"]')?.focus();
            panel.dataset.importOptionsAvailable = typeof options.onImportSources === 'function'
                ? 'true'
                : 'false';
        }

        function applyRequestOptions(form, requestOptions = {}) {
            const setValue = (name, value) => {
                const control = form.elements.namedItem(name);
                if (control instanceof HTMLSelectElement && value != null) {
                    control.value = String(value);
                }
                if (control instanceof HTMLInputElement && control.type === 'checkbox') {
                    control.checked = value === true;
                }
            };
            setValue('pdf_mode', requestOptions.pdf_mode);
            const strategies = requestOptions.pdf_strategies || requestOptions.strategies || [];
            setValue(
                'pdf_strategy',
                strategies.length === 1 && strategies[0] === 'pdf_ocr'
                    ? 'ocr'
                    : strategies.length === 1 && strategies[0] === 'pdf_text'
                        ? 'local_text'
                        : ''
            );
            if (requestOptions.capture_ocr_images != null) {
                setValue('capture_ocr_images', String(requestOptions.capture_ocr_images));
            }
            setValue('include_ocr_blocks', requestOptions.include_ocr_blocks);
            setValue('extract_ocr_header', requestOptions.extract_ocr_header);
            setValue('extract_ocr_footer', requestOptions.extract_ocr_footer);
            setValue('ocr_table_format', requestOptions.ocr_table_format);
            setValue('ocr_confidence', requestOptions.ocr_confidence);
            importOptions.preserve(form, requestOptions);
        }

        function updateDestination(overlay, destination) {
            const form = overlay.querySelector('[data-vault-explorer-import-form]');
            if (!(form instanceof HTMLFormElement)) return;
            const input = form.elements.namedItem('destination');
            if (input instanceof HTMLInputElement) input.value = destination;
            const label = form.querySelector('[data-vault-explorer-import-destination]');
            if (label) label.textContent = destination || 'Vault root';
        }

        async function submit(overlay, form, options) {
            if (busy || typeof options.onImportSources !== 'function') return;
            const status = form.querySelector('[data-vault-explorer-form-status]');
            const submitButton = form.querySelector('button[type="submit"]');
            let sources = JSON.parse(form.dataset.sources || '[]');
            const urlInput = form.elements.namedItem('url');
            if (urlInput instanceof HTMLInputElement) sources = [urlInput.value.trim()];
            const destinationInput = form.elements.namedItem('destination');
            const payload = {
                ...importOptions.read(form),
                sources,
                destination: destinationInput instanceof HTMLInputElement
                    ? destinationInput.value
                    : '',
            };
            busy = true;
            callbacks.syncInteractionLocks();
            if (submitButton instanceof HTMLButtonElement) submitButton.disabled = true;
            if (status) status.textContent = payload.queue_only ? 'Queueing import…' : 'Importing…';
            try {
                const result = await options.onImportSources(payload);
                const jobs = Array.isArray(result?.jobs_created) ? result.jobs_created : [];
                renderResult(status, jobs, payload.queue_only);
                const output = firstOutput(jobs);
                if (output) await callbacks.refreshExplorer(overlay, options, output);
                if (
                    !payload.queue_only
                    && jobs.some((job) => !isTerminal(job.status))
                    && typeof options.onGetImportJob === 'function'
                ) {
                    const generation = ++pollGeneration;
                    void pollJobs(overlay, status, jobs, options, generation);
                }
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
            const pending = jobs.filter((job) => !isTerminal(job.status));
            const summary = queueOnly
                ? `${jobs.length} import job${jobs.length === 1 ? '' : 's'} queued.`
                : pending.length
                    ? `${jobs.length} import job${jobs.length === 1 ? '' : 's'} accepted.`
                : `${jobs.length - failures.length} import${jobs.length - failures.length === 1 ? '' : 's'} completed.`;
            status.innerHTML = `
                <div class="${failures.length ? 'state-warning' : 'state-success'}">${escapeHtml(summary)}</div>
                ${outputs.map((path) => `<div class="cell-mono">${escapeHtml(path)}</div>`).join('')}
                ${failures.map((job) => `<div class="state-error">${escapeHtml(job.error || 'Import failed.')}</div>`).join('')}
            `;
        }

        async function pollJobs(overlay, status, initialJobs, options, generation) {
            let jobs = initialJobs;
            for (let attempt = 0; attempt < 240; attempt += 1) {
                if (generation !== pollGeneration || !overlay.isConnected) return;
                await new Promise((resolve) => window.setTimeout(resolve, 500));
                if (generation !== pollGeneration || !overlay.isConnected) return;
                try {
                    jobs = await Promise.all(jobs.map((job) => (
                        isTerminal(job.status) ? job : options.onGetImportJob(job.id)
                    )));
                    renderResult(status, jobs, false);
                    if (jobs.every((job) => isTerminal(job.status))) {
                        const output = firstOutput(jobs);
                        if (output) await callbacks.refreshExplorer(overlay, options, output);
                        return;
                    }
                } catch (error) {
                    if (status) {
                        status.innerHTML = `<span class="state-warning">Import accepted; status refresh failed: ${escapeHtml(error.message)}</span>`;
                    }
                    return;
                }
            }
            if (status) status.textContent = 'Import is still processing. Check Dashboard → Import for status.';
        }

        function isTerminal(status) {
            return ['completed', 'failed', 'cancelled'].includes(String(status || ''));
        }

        function firstOutput(jobs) {
            return jobs.flatMap((job) => job.outputs || [])[0] || '';
        }

        function isBusy() {
            return busy;
        }

        function reset() {
            busy = false;
            pollGeneration += 1;
        }

        return Object.freeze({
            isBusy,
            reset,
            showFiles,
            showUrl,
            submit,
            updateDestination,
        });
    }

    function supportsPath(path) {
        const name = String(path || '').toLowerCase();
        const dot = name.lastIndexOf('.');
        return dot >= 0 && supportedExtensions().has(name.slice(dot));
    }

    window.VaultExplorerImports = Object.freeze({
        create: createVaultExplorerImportsController,
        supportsPath,
    });
})(window);
