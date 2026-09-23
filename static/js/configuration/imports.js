(function configurationFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, resources, state, timers } = runtime;
    const { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice } = helpers;

    function updateImportOcrAvailability() {
        if (!elements.importPdfModeSelect || !elements.importPdfStrategySelect) return;
        const capability = window.App?.metadata?.ingestion_capabilities?.pdf_ocr;
        const isPageImagesMode = (elements.importPdfModeSelect?.value || 'markdown') === 'page_images';
        const selectedStrategy = elements.importPdfStrategySelect.value || 'default';
        const defaultOrder = Array.isArray(capability?.default_order)
            ? capability.default_order.map(value => String(value))
            : [];
        const defaultIncludesOcr = defaultOrder.includes('pdf_ocr');
        const selectedPathUsesOcr = selectedStrategy === 'ocr'
            || (selectedStrategy === 'default' && defaultIncludesOcr);
        const missingRequirements = Array.isArray(capability?.missing)
            ? capability.missing.map(value => String(value))
            : ['OCR capability metadata'];
        const ocrAvailable = capability?.available === true;
        const disableEnrichments = !ocrAvailable || !selectedPathUsesOcr || isPageImagesMode;
        const disabledReason = !ocrAvailable
            ? `OCR unavailable: configure ${missingRequirements.join(', ')}`
            : 'Select a conversion strategy that can invoke Mistral OCR.';
        const enrichmentControls = [
            elements.importIncludeOcrBlocksCheckbox,
            elements.importExtractOcrHeaderCheckbox,
            elements.importExtractOcrFooterCheckbox,
            elements.importOcrTableFormatSelect,
            elements.importOcrConfidenceSelect
        ].filter(Boolean);

        elements.importMarkdownOptions?.classList.toggle('hidden', isPageImagesMode);
        elements.importPageImageOptions?.classList.toggle('hidden', !isPageImagesMode);
        elements.importPdfOcrEnrichments?.classList.toggle('hidden', !selectedPathUsesOcr);
        const ocrOption = elements.importPdfStrategySelect.querySelector('option[value="ocr"]');
        if (ocrOption instanceof HTMLOptionElement) ocrOption.disabled = !ocrAvailable;
        if (elements.importCaptureOcrImagesCheckbox) {
            elements.importCaptureOcrImagesCheckbox.disabled = !ocrAvailable || isPageImagesMode;
        }
        enrichmentControls.forEach(control => { control.disabled = disableEnrichments; });
        if (disableEnrichments) {
            enrichmentControls.forEach(control => { control.title = disabledReason; });
            if (!ocrAvailable && elements.importCaptureOcrImagesCheckbox) {
                elements.importCaptureOcrImagesCheckbox.title = disabledReason;
            }
        } else {
            enrichmentControls.forEach(control => { control.removeAttribute('title'); });
            elements.importCaptureOcrImagesCheckbox?.removeAttribute('title');
        }
        if (disableEnrichments) {
            if ((!ocrAvailable || isPageImagesMode) && elements.importCaptureOcrImagesCheckbox) {
                elements.importCaptureOcrImagesCheckbox.checked = false;
            }
            enrichmentControls.forEach(control => {
                if (control instanceof HTMLInputElement) control.checked = false;
                if (control instanceof HTMLSelectElement) control.value = '';
            });
        }
        if (elements.importPdfStrategyHelp) {
            const orderLabel = defaultOrder.length ? defaultOrder.join(' → ') : 'no strategies configured';
            if (selectedStrategy === 'local_text') {
                elements.importPdfStrategyHelp.textContent = 'Uses local PDF text extraction only. PDF OCR enrichments do not apply.';
            } else if (selectedStrategy === 'ocr') {
                elements.importPdfStrategyHelp.textContent = ocrAvailable
                    ? 'Uses Mistral OCR only. OCR enrichment options apply to this import.'
                    : disabledReason;
            } else {
                const ocrIndex = defaultOrder.indexOf('pdf_ocr');
                elements.importPdfStrategyHelp.textContent = ocrIndex < 0
                    ? `Configured default: ${orderLabel}. PDF OCR enrichments do not apply.`
                    : ocrIndex === 0
                        ? `Configured default: ${orderLabel}. OCR runs first, so enrichment options apply.`
                        : `Configured default: ${orderLabel}. Enrichment options apply only if earlier strategies fall through to OCR.`;
            }
            const defaultOption = elements.importPdfStrategySelect.querySelector('option[value="default"]');
            if (defaultOption instanceof HTMLOptionElement) {
                defaultOption.textContent = `Configured default (${orderLabel})`;
            }
        }
    }


    function renderImportVaults() {
        const select = elements.importVaultSelect;
        if (!select) return;
        const selectedVault = select.value;
        select.innerHTML = '<option value="">Select vault…</option>';
        if (!state.importVaults || state.importVaults.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No vaults detected';
            opt.disabled = true;
            select.appendChild(opt);
            return;
        }
        state.importVaults.forEach((vault) => {
            const opt = document.createElement('option');
            opt.value = vault;
            opt.textContent = vault;
            select.appendChild(opt);
        });
        if (state.importVaults.includes(selectedVault)) {
            select.value = selectedVault;
        }
    }

    function handleImportVaultChange() {
        state.importResults = null;
        state.importUrlResult = null;
        renderImportResults();
        if (state.isLoadingImportJobs) {
            state.pendingImportJobsReload = true;
        } else {
            actions.loadImportJobs();
        }
    }

    async function loadImportVaults(force = false) {
        if (state.isLoadingImportVaults) return;
        state.isLoadingImportVaults = true;
        setStatus(elements.importStatus, 'Loading vaults…', 'info');

        // Prefer cached metadata from main app if available
        const cachedVaults = window.App && window.App.metadata && Array.isArray(window.App.metadata.vaults)
            ? window.App.metadata.vaults
            : null;
        if (cachedVaults && !force) {
            state.importVaults = cachedVaults;
            renderImportVaults();
            setStatus(elements.importStatus, '', 'info');
        }

        try {
            // Always refresh from API to stay current
            const response = await fetch('api/metadata');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importVaults = Array.isArray(data?.vaults) ? data.vaults : [];
            renderImportVaults();
            setStatus(elements.importStatus, '', 'info');
            // Keep cache in sync for other modules
            window.App = window.App || {};
            window.App.metadata = data;
        } catch (error) {
            if (!cachedVaults) {
                state.importVaults = [];
                renderImportVaults();
            }
            setStatus(elements.importStatus, `Failed to load vaults: ${error.message}`, 'error');
        } finally {
            state.isLoadingImportVaults = false;
        }
    }

    async function handleImportVaultRescan() {
        if (!elements.importRefreshVaultsBtn || state.isLoadingImportVaults) return;
        const btn = elements.importRefreshVaultsBtn;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Refresh Vaults';
        btn.disabled = true;
        setIconButtonLabel(btn, 'Rescanning vaults...');
        setStatus(elements.importStatus, 'Rescanning vaults…', 'info');
        try {
            const response = await fetch('api/vaults/rescan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            // Refresh global status/metadata if caller provided callbacks
            if (callbacks.refreshStatus) await callbacks.refreshStatus();
            if (callbacks.refreshMetadata) await callbacks.refreshMetadata();
            // Reload vault list for import select
            await loadImportVaults(true);
            setStatus(elements.importStatus, 'Vaults refreshed.', 'success');
        } catch (error) {
            setStatus(elements.importStatus, `Rescan failed: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
        }
    }

    function normalizeOutputPaths(outputs) {
        if (!Array.isArray(outputs)) return [];
        const seen = new Set();
        const normalized = [];
        outputs.forEach((value) => {
            const path = String(value || '').trim();
            if (!path || seen.has(path)) return;
            seen.add(path);
            normalized.push(path);
        });
        return normalized;
    }

    function summarizeImportOutputs(outputs) {
        const paths = normalizeOutputPaths(outputs);
        if (!paths.length) {
            return {
                destinationLabel: 'No output files',
                filesLabel: '0 files',
            };
        }

        const directorySegments = paths.map((path) => {
            const slashIndex = path.lastIndexOf('/');
            const folder = slashIndex >= 0 ? path.slice(0, slashIndex) : '';
            return folder ? folder.split('/').filter(Boolean) : [];
        });

        let commonDir = directorySegments[0] ? directorySegments[0].slice() : [];
        for (let i = 1; i < directorySegments.length; i += 1) {
            const current = directorySegments[i];
            let prefixLen = 0;
            while (
                prefixLen < commonDir.length &&
                prefixLen < current.length &&
                commonDir[prefixLen] === current[prefixLen]
            ) {
                prefixLen += 1;
            }
            commonDir = commonDir.slice(0, prefixLen);
            if (!commonDir.length) break;
        }

        // Collapse companion assets to a useful import destination summary.
        const destinationSegments = (
            commonDir.length >= 2 && commonDir[0] === 'Imported'
                ? commonDir.slice(0, 2)
                : commonDir
        );
        const destinationLabel = destinationSegments.length
            ? destinationSegments.join('/')
            : '(vault root)';

        const extensionCounts = new Map();
        paths.forEach((path) => {
            const slashIndex = path.lastIndexOf('/');
            const filename = slashIndex >= 0 ? path.slice(slashIndex + 1) : path;
            const dotIndex = filename.lastIndexOf('.');
            const extension = dotIndex > 0
                ? filename.slice(dotIndex + 1).toLowerCase()
                : 'no-ext';
            extensionCounts.set(extension, (extensionCounts.get(extension) || 0) + 1);
        });

        const typeParts = Array.from(extensionCounts.entries())
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .map(([ext, count]) => `${count} ${ext}`);

        const filesLabel = `${paths.length} file${paths.length === 1 ? '' : 's'} (${typeParts.join(', ')})`;

        return { destinationLabel, filesLabel };
    }

    function renderImportOutputLinks(outputs, vault, { compact = false } = {}) {
        const paths = normalizeOutputPaths(outputs);
        if (!paths.length) return '<span class="subtle">No output files</span>';
        const listClass = compact ? 'space-y-1' : 'space-y-1 ml-4';
        return `
            <ul class="${listClass}">
                ${paths.map((path) => `
                    <li class="break-words">
                        <button
                            type="button"
                            class="vault-file-link vault-file-link-code text-left break-all"
                            data-import-output-path="${escapeHtml(path)}"
                            data-import-output-vault="${escapeHtml(vault || '')}"
                            title="Open ${escapeHtml(path)} in the vault viewer"
                        >${escapeHtml(path)}</button>
                    </li>
                `).join('')}
            </ul>
        `;
    }

    function renderImportSource(source) {
        const value = String(source || 'unknown');
        let isWebUrl = false;
        try {
            const parsed = new URL(value);
            isWebUrl = parsed.protocol === 'http:' || parsed.protocol === 'https:';
        } catch (_error) {
            // Local inbox paths are expected and remain plain text.
        }
        if (!isWebUrl) return escapeHtml(value);
        return `<a class="vault-file-link break-all" href="${escapeHtml(value)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>`;
    }


    function renderImportResults() {
        if (!elements.importResults) return;
        const results = state.importResults;
        const urlResult = state.importUrlResult;

        let sections = [];

        if (results) {
            const jobs = Array.isArray(results.jobs_created) ? results.jobs_created : [];
            const skipped = Array.isArray(results.skipped) ? results.skipped : [];
            let html = '';
            if (jobs.length > 0) {
                html += `<div class="space-y-2"><div class="font-medium text-txt-primary">File imports (${jobs.length})</div>`;
                html += '<ul class="list-disc list-inside space-y-1">';
                html += jobs
                    .map((job) => {
                        const outputSummary = summarizeImportOutputs(job.outputs);
                        const status = job.status || 'queued';
                        const source = job.source_uri || 'unknown';
                        return `
                            <li>
                                <span class="text-txt-primary font-medium">${renderImportSource(source)}</span>
                                <span class="subtle">(${escapeHtml(status)})</span>
                                <div class="text-xs text-txt-secondary ml-4">
                                    Destination: <span class="text-txt-primary">${escapeHtml(outputSummary.destinationLabel)}</span>
                                </div>
                                <div class="text-xs text-txt-secondary ml-4">
                                    Files: ${escapeHtml(outputSummary.filesLabel)}
                                </div>
                                <div class="text-xs text-txt-secondary ml-4">Import path:</div>
                                ${renderImportOutputLinks(job.outputs, job.vault)}
                            </li>
                        `;
                    })
                    .join('');
                html += '</ul></div>';
            }
            if (skipped.length > 0) {
                html += `<div class="space-y-2 pt-3 border-t border-border-primary"><div class="font-medium text-txt-primary">Skipped (${skipped.length})</div>`;
                html += '<ul class="list-disc list-inside space-y-1">';
                html += skipped.map((name) => `<li>${escapeHtml(name)}</li>`).join('');
                html += '</ul></div>';
            }
            if (html) sections.push(html);
        }

        if (urlResult) {
            const outputSummary = summarizeImportOutputs(urlResult.outputs);
            const status = urlResult.status || 'unknown';
            const error = urlResult.error;
            const source = urlResult.source_uri || urlResult.url || 'unknown';

            let html = `<div class="space-y-2"><div class="font-medium text-txt-primary">Latest URL import</div>`;
            html += `<div class="text-sm"><span class="text-txt-primary font-medium">${renderImportSource(source)}</span> <span class="subtle">(${escapeHtml(status)})</span></div>`;
            html += `<div class="text-sm text-txt-secondary">Destination: <span class="text-txt-primary">${escapeHtml(outputSummary.destinationLabel)}</span></div>`;
            html += `<div class="text-sm text-txt-secondary">Files: ${escapeHtml(outputSummary.filesLabel)}</div>`;
            html += '<div class="text-sm text-txt-secondary">Import path:</div>';
            html += renderImportOutputLinks(urlResult.outputs, urlResult.vault);
            if (error) {
                html += `<div class="text-sm state-error">Error: ${escapeHtml(error)}</div>`;
            }
            html += '</div>';
            sections.push(html);
        }

        elements.importResults.innerHTML = sections.join('<div class="pt-3 border-t border-border-primary"></div>') || 'No imports yet.';
    }

    async function handleImportScan() {
        if (!elements.importScanBtn || state.isScanningImport) return;

        const vault = elements.importVaultSelect?.value || '';
        if (!vault) {
            setStatus(elements.importStatus, 'Select a vault before scanning.', 'warning');
            return;
        }

        const queueOnly = Boolean(elements.importQueueCheckbox?.checked);
        const captureOcrImages = Boolean(elements.importCaptureOcrImagesCheckbox?.checked);
        const pdfMode = (elements.importPdfModeSelect?.value || 'markdown').trim();
        const pdfStrategies = selectedPdfStrategyOverride();

        const payload = { vault, queue_only: queueOnly };
        if (pdfMode === 'page_images') {
            payload.pdf_mode = 'page_images';
        }
        if (pdfStrategies) payload.strategies = pdfStrategies;
        if (captureOcrImages) {
            payload.capture_ocr_images = true;
        }
        addOcrEnrichmentOptions(payload);

        state.isScanningImport = true;
        // Reset URL status when starting a file import
        state.importUrlResult = null;
        renderImportResults();
        const btn = elements.importScanBtn;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Import Files';
        btn.disabled = true;
        setIconButtonLabel(btn, queueOnly ? 'Queueing import jobs...' : 'Importing files...');
        setStatus(
            elements.importStatus,
            queueOnly ? 'Queueing import jobs…' : 'Scanning import folder and processing…',
            'info'
        );

        try {
            const response = await fetch('api/import/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importResults = data;
            renderImportResults();
            await actions.loadImportJobs({ silent: true });
            setStatus(
                elements.importStatus,
                queueOnly ? 'Jobs queued.' : 'Import completed.',
                'success'
            );
            if (callbacks.refreshStatus) callbacks.refreshStatus();
        } catch (error) {
            setStatus(elements.importStatus, `Import failed: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
            state.isScanningImport = false;
        }
    }

    async function handleImportUrl() {
        if (!elements.importUrlSubmit || state.isImportingUrl) return;

        const vault = elements.importVaultSelect?.value || '';
        const url = (elements.importUrlInput?.value || '').trim();
        if (!vault || !url) {
            setStatus(elements.importStatus, 'Select a vault and enter a URL.', 'warning');
            return;
        }

        // Reset file status when starting a URL import
        state.importResults = null;
        renderImportResults();
        state.isImportingUrl = true;
        const btn = elements.importUrlSubmit;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Import URL';
        btn.disabled = true;
        setIconButtonLabel(btn, 'Ingesting URL...');
        setStatus(elements.importStatus, 'Ingesting URL…', 'info');

        const captureOcrImages = Boolean(elements.importCaptureOcrImagesCheckbox?.checked);
        const pdfMode = (elements.importPdfModeSelect?.value || 'markdown').trim();
        const pdfStrategies = selectedPdfStrategyOverride();
        const payload = { vault, url, clean_html: true, pdf_mode: pdfMode };
        if (pdfStrategies) payload.pdf_strategies = pdfStrategies;
        if (captureOcrImages) payload.capture_ocr_images = true;
        addOcrEnrichmentOptions(payload);

        try {
            const response = await fetch('api/import/url', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importUrlResult = data;
            await actions.loadImportJobs({ silent: true });
            setStatus(elements.importStatus, 'URL ingested.', data.error ? 'warning' : 'success');
            if (callbacks.refreshStatus) callbacks.refreshStatus();
            renderImportResults();
        } catch (error) {
            setStatus(elements.importStatus, `Import failed: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
            state.isImportingUrl = false;
        }
    }

    function addOcrEnrichmentOptions(payload) {
        if (elements.importIncludeOcrBlocksCheckbox?.checked) payload.include_ocr_blocks = true;
        if (elements.importExtractOcrHeaderCheckbox?.checked) payload.extract_ocr_header = true;
        if (elements.importExtractOcrFooterCheckbox?.checked) payload.extract_ocr_footer = true;
        const tableFormat = (elements.importOcrTableFormatSelect?.value || '').trim();
        const confidence = (elements.importOcrConfidenceSelect?.value || '').trim();
        if (tableFormat) payload.ocr_table_format = tableFormat;
        if (confidence) payload.ocr_confidence = confidence;
    }

    function selectedPdfStrategyOverride() {
        const selected = elements.importPdfStrategySelect?.value || 'default';
        if (selected === 'local_text') return ['pdf_text'];
        if (selected === 'ocr') return ['pdf_ocr'];
        return null;
    }


    Object.assign(actions, {
        updateImportOcrAvailability,
        renderImportVaults,
        handleImportVaultChange,
        loadImportVaults,
        handleImportVaultRescan,
        normalizeOutputPaths,
        summarizeImportOutputs,
        renderImportOutputLinks,
        renderImportSource,
        renderImportResults,
        handleImportScan,
        handleImportUrl,
        addOcrEnrichmentOptions,
        selectedPdfStrategyOverride
    });
}(window, document));
