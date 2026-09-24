(function configurationImportsFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, elements, helpers, state } = runtime;
    const { notifyConfigChanged, safeJson, setIconButtonLabel, setStatus } = helpers;

    function settingValue(key, fallback) {
        const setting = state.settings.find((item) => item.key === key);
        return setting ? setting.value : fallback;
    }

    function normalizeOutputPaths(outputs) {
        if (!Array.isArray(outputs)) return [];
        return Array.from(new Set(outputs.map(value => String(value || '').trim()).filter(Boolean)));
    }

    function renderImportOutputLinks(outputs, vault, { compact = false } = {}) {
        const paths = normalizeOutputPaths(outputs);
        if (!paths.length) return '<span class="subtle">No output files</span>';
        const listClass = compact ? 'space-y-1' : 'space-y-1 ml-4';
        return `<ul class="${listClass}">${paths.map((path) => `
            <li class="break-words">
                <button type="button" class="vault-file-link vault-file-link-code text-left break-all"
                    data-import-output-path="${helpers.escapeHtml(path)}"
                    data-import-output-vault="${helpers.escapeHtml(vault || '')}"
                    title="Open ${helpers.escapeHtml(path)} in the vault viewer">${helpers.escapeHtml(path)}</button>
            </li>`).join('')}</ul>`;
    }

    function renderImportSource(source, vault = '') {
        const value = String(source || 'unknown');
        try {
            const parsed = new URL(value);
            if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
                return `<a class="vault-file-link break-all" href="${helpers.escapeHtml(value)}" target="_blank" rel="noopener noreferrer">${helpers.escapeHtml(value)}</a>`;
            }
        } catch (_error) {
            // Vault-relative sources are rendered as Explorer links.
        }
        return `<button type="button" class="vault-file-link vault-file-link-code text-left break-all"
            data-import-source-path="${helpers.escapeHtml(value)}"
            data-import-source-vault="${helpers.escapeHtml(vault)}"
            title="Show ${helpers.escapeHtml(value)} in the Vault Explorer">${helpers.escapeHtml(value)}</button>`;
    }

    function updateImportOcrAvailability() {
        if (!elements.importPdfModeSelect || !elements.importPdfStrategySelect) return;
        const configuredStrategies = settingValue(
            'ingestion_pdf_default_strategies',
            '["pdf_ocr", "pdf_text"]'
        );
        let strategies = ['pdf_ocr', 'pdf_text'];
        try {
            const parsed = JSON.parse(String(configuredStrategies));
            if (Array.isArray(parsed)) strategies = parsed.map((value) => String(value));
        } catch (_) {
            strategies = ['pdf_ocr', 'pdf_text'];
        }
        const strategyValue = strategies.length === 1 && strategies[0] === 'pdf_text'
            ? 'local_text'
            : strategies.length === 1 && strategies[0] === 'pdf_ocr'
                ? 'ocr'
                : 'default';
        const pdfMode = String(settingValue('ingestion_pdf_default_mode', 'markdown'));
        const captureImages = String(
            settingValue('ingestion_ocr_capture_images', 'false')
        ).toLowerCase() === 'true';
        elements.importPdfModeSelect.value = pdfMode === 'page_images'
            ? 'page_images'
            : 'markdown';
        elements.importPdfStrategySelect.value = strategyValue;
        if (elements.importCaptureOcrImagesCheckbox) {
            elements.importCaptureOcrImagesCheckbox.checked = captureImages;
        }
        updateImportDefaultControls();
    }

    function updateImportDefaultControls() {
        if (!elements.importPdfModeSelect || !elements.importPdfStrategySelect) return;
        const capability = window.App?.metadata?.ingestion_capabilities?.pdf_ocr;
        const ocrAvailable = capability?.available === true;
        const missing = Array.isArray(capability?.missing)
            ? capability.missing.join(', ')
            : 'OCR configuration';
        const pageImages = elements.importPdfModeSelect.value === 'page_images';
        const ocrOption = elements.importPdfStrategySelect.querySelector('option[value="ocr"]');
        if (ocrOption instanceof HTMLOptionElement) ocrOption.disabled = !ocrAvailable;
        if (elements.importCaptureOcrImagesCheckbox) {
            elements.importCaptureOcrImagesCheckbox.disabled = !ocrAvailable || pageImages;
        }
        elements.importMarkdownOptions?.classList.toggle(
            'hidden',
            pageImages
        );
        elements.importPageImageOptions?.classList.toggle(
            'hidden',
            !pageImages
        );
        if (elements.importPdfStrategyHelp) {
            elements.importPdfStrategyHelp.textContent = ocrAvailable
                ? 'This strategy becomes the default for new PDF imports.'
                : `OCR unavailable: configure ${missing}. Local PDF text remains available.`;
        }
    }

    function renderImportVaults() {
        const select = elements.importVaultSelect;
        if (!select) return;
        const selectedVault = select.value;
        select.innerHTML = '<option value="">Select vault…</option>';
        if (!state.importVaults?.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'No vaults detected';
            option.disabled = true;
            select.appendChild(option);
            return;
        }
        state.importVaults.forEach((vault) => {
            const option = document.createElement('option');
            option.value = vault;
            option.textContent = vault;
            select.appendChild(option);
        });
        if (state.importVaults.includes(selectedVault)) select.value = selectedVault;
    }

    function handleImportVaultChange() {
        if (state.isLoadingImportJobs) {
            state.pendingImportJobsReload = true;
        } else {
            actions.loadImportJobs();
        }
    }

    async function loadImportVaults(force = false) {
        if (state.isLoadingImportVaults) return;
        state.isLoadingImportVaults = true;
        const cachedVaults = Array.isArray(window.App?.metadata?.vaults)
            ? window.App.metadata.vaults
            : null;
        if (cachedVaults && !force) {
            state.importVaults = cachedVaults;
            renderImportVaults();
        }
        try {
            const response = await fetch('api/metadata');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importVaults = Array.isArray(data?.vaults) ? data.vaults : [];
            renderImportVaults();
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
        const button = elements.importRefreshVaultsBtn;
        button.disabled = true;
        setIconButtonLabel(button, 'Rescanning vaults...');
        try {
            const response = await fetch('api/vaults/rescan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            await callbacks.refreshStatus?.();
            await callbacks.refreshMetadata?.();
            await loadImportVaults(true);
            setStatus(elements.importStatus, 'Vaults refreshed.', 'success');
        } catch (error) {
            setStatus(elements.importStatus, `Rescan failed: ${error.message}`, 'error');
        } finally {
            button.disabled = false;
            setIconButtonLabel(button, 'Refresh Vaults');
        }
    }

    function openImportExplorer() {
        const vault = elements.importVaultSelect?.value || '';
        if (!vault) {
            setStatus(elements.importStatus, 'Select a vault first.', 'warning');
            return;
        }
        callbacks.openExplorer?.({ vaultName: vault });
    }

    async function saveImportDefaults() {
        if (state.isSavingImportDefaults) return;
        const strategy = elements.importPdfStrategySelect?.value || 'default';
        const values = {
            ingestion_pdf_default_mode: elements.importPdfModeSelect?.value || 'markdown',
            ingestion_pdf_default_strategies: strategy === 'ocr'
                ? '["pdf_ocr"]'
                : strategy === 'local_text'
                    ? '["pdf_text"]'
                    : '["pdf_ocr", "pdf_text"]',
            ingestion_ocr_capture_images: elements.importCaptureOcrImagesCheckbox?.checked
                ? 'true'
                : 'false',
        };
        state.isSavingImportDefaults = true;
        if (elements.importDefaultsSaveBtn) elements.importDefaultsSaveBtn.disabled = true;
        setStatus(elements.importStatus, 'Saving import defaults…', 'info');
        try {
            for (const [key, value] of Object.entries(values)) {
                const response = await fetch(
                    `api/system/settings/general/${encodeURIComponent(key)}`,
                    {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ value }),
                    }
                );
                const result = await safeJson(response);
                if (!response.ok) throw new Error(result?.message || `HTTP ${response.status}`);
                state.settings = state.settings.map((item) => (
                    item.key === result.key ? result : item
                ));
            }
            await notifyConfigChanged();
            setStatus(elements.importStatus, 'Import defaults saved.', 'success');
        } catch (error) {
            setStatus(elements.importStatus, `Failed to save defaults: ${error.message}`, 'error');
        } finally {
            state.isSavingImportDefaults = false;
            if (elements.importDefaultsSaveBtn) elements.importDefaultsSaveBtn.disabled = false;
        }
    }

    Object.assign(actions, {
        handleImportVaultChange,
        handleImportVaultRescan,
        loadImportVaults,
        openImportExplorer,
        renderImportVaults,
        renderImportOutputLinks,
        renderImportSource,
        saveImportDefaults,
        updateImportDefaultControls,
        updateImportOcrAvailability,
    });
}(window, document));
