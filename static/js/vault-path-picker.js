(function vaultPathPickerModule(window, document) {
    function createVaultPathPickerController({ elements, icons, utils }) {
        const { escapeHtml } = utils;
        let activePickerId = '';
        let activeOnClose = null;
        let activeOptions = null;
        let rootLoadGeneration = 0;
        let rootAbortController = null;
        const explorerSearch = window.VaultExplorerSearch.create({ utils });
        const explorerActions = window.VaultExplorerActions.create({
            icons,
            utils,
            callbacks: {
                batchMutationCompleted,
                isReadOnly,
                mutationCompleted,
                refreshExplorer,
                setStatus,
                importDestinationSelected: (overlay, destination) => (
                    explorerImports.updateDestination(overlay, destination)
                ),
                syncInteractionLocks,
                workspacePath,
            },
        });
        const explorerImports = window.VaultExplorerImports.create({
            utils,
            callbacks: {
                closeActionPanel: explorerActions.closeActionPanel,
                beginDestinationMode: explorerActions.beginDestinationMode,
                refreshExplorer,
                syncInteractionLocks,
            },
        });
        const explorerBatchMoves = window.VaultExplorerBatchMoves.create({
            icons,
            utils,
            callbacks: {
                batchMutationCompleted,
                beginDestinationMode: explorerActions.beginDestinationMode,
                closeActionPanel: explorerActions.closeActionPanel,
                destinationSnapshot: explorerActions.destinationSnapshot,
                isReadOnly,
                refreshExplorer,
                selectDestination: explorerActions.selectDestination,
                workspacePath,
            },
        });
        const explorer = window.VaultExplorerController.create({
            icons,
            utils,
            callbacks: {
                expandDirectory,
                handleBatchMove: explorerBatchMoves.show,
                handleImportAction,
                handleMutationAction: explorerActions.handleAction,
                isBusy: isExplorerBusy,
                isReadOnly,
                refreshExplorer,
                setStatus,
                showSelection: showExplorerSelection,
                supportsImportPath: window.VaultExplorerImports.supportsPath,
                navigateLocation: navigateExplorerLocation,
                workspacePath,
            },
        });

        function selectedVault() {
            return elements.vaultSelector?.value || '';
        }

        function workspacePath() {
            return (elements.workspacePathInput?.value || '').trim();
        }

        function isReadOnly(options) {
            return Boolean(options?.isReadOnly?.());
        }

        function isExplorerBusy() {
            return explorerActions.isBusy() || explorerImports.isBusy();
        }

        function mutationCompleted({ operation, sourcePath, targetPath, kind }) {
            explorer.mutationCompleted({ operation, sourcePath, targetPath, kind });
        }

        function batchMutationCompleted(results) {
            explorer.batchMutationCompleted(results);
        }

        function open(options = {}) {
            const vault = options.vaultName || selectedVault();
            if (!vault) {
                alert(options.missingVaultMessage || 'Select a vault first.');
                return;
            }
            close();
            const id = options.id || 'vault-path-picker-modal';
            activePickerId = id;
            activeOnClose = typeof options.onClose === 'function' ? options.onClose : null;
            activeOptions = options;
            const mode = options.mode === 'directories' ? 'directories' : 'files';
            const titleId = `${id}-title`;
            const showSearch = mode === 'files';
            const overlay = document.createElement('div');
            overlay.id = id;
            overlay.className = 'app-modal-overlay fixed inset-0 z-50 flex bg-black/40';
            overlay.innerHTML = `
                <section class="app-modal-panel relative flex flex-col" role="dialog" aria-modal="true" ${options.explorer ? 'aria-label="Vault Explorer"' : `aria-labelledby="${escapeHtml(titleId)}"`}>
                    <div class="app-modal-header vault-path-picker-header${options.explorer ? ' vault-explorer-modal-header' : ''} flex-none">
                        ${options.explorer ? `
                            <div class="vault-explorer-header-location" data-vault-explorer-header-location></div>
                        ` : `
                            <div class="app-modal-title-block">
                                <h2 id="${escapeHtml(titleId)}" class="text-lg font-semibold text-txt-primary">${escapeHtml(options.title || 'Choose Path')}</h2>
                                <p class="mt-1 text-xs text-txt-secondary cell-mono">${escapeHtml(options.subtitle || vault)}</p>
                            </div>
                            ${showSearch ? `<div class="file-reference-toolbar vault-explorer-header-search">
                                <select data-vault-path-picker-search-mode class="file-reference-scope" aria-label="Search type">
                                    <option value="name">Files</option>
                                    <option value="content">Contents</option>
                                </select>
                                <select data-vault-path-picker-scope class="file-reference-scope" aria-label="Search scope">
                                    <option value="workspace">Workspace only</option>
                                    <option value="active">Current folder</option>
                                    <option value="vault">Entire vault</option>
                                </select>
                                <input data-vault-path-picker-query type="search" class="file-reference-search" placeholder="${escapeHtml(options.searchPlaceholder || 'Search workspace...')}" aria-label="Search files" />
                            </div>` : ''}
                        `}
                        <div class="app-modal-actions">
                            <button type="button" class="ui-icon-button is-compact" data-vault-path-picker-close aria-label="Close" title="Close">${icons.X_ICON_SVG}</button>
                        </div>
                    </div>
                    <div class="vault-path-picker-body p-4 flex-1 min-h-0 flex flex-col gap-3">
                        ${options.selectedLabel ? `
                            <div class="p-3 rounded border border-border-primary bg-app-elevated">
                                <div class="text-xs uppercase text-txt-secondary">${escapeHtml(options.selectedLabel)}</div>
                                <div class="mt-1 text-sm cell-mono text-txt-primary">${escapeHtml(options.selectedPath || 'None')}</div>
                                ${options.workspaceSelectionMode
                                    ? `<div class="mt-2 text-xs text-txt-secondary">Choose <strong>Use</strong> beside a folder to ${options.workspaceRecovery ? 'replace it' : 'set a new workspace'}.</div>`
                                    : ''}
                            </div>
                        ` : ''}
                        ${options.explorer ? `
                            <input type="file" class="hidden" data-vault-explorer-upload-input multiple />
                            <div class="vault-explorer-toolbar">
                                <div class="vault-explorer-search-control">
                                    <input data-vault-path-picker-query type="search" class="file-reference-search"
                                        placeholder="Search this folder..." aria-label="Search files" />
                                    <input data-vault-path-picker-search-mode type="hidden" value="name" />
                                    <button type="button" class="vault-explorer-search-mode-toggle"
                                        data-vault-explorer-search-mode-toggle aria-haspopup="menu" aria-expanded="false"
                                        aria-label="Search mode" title="Search mode">Files</button>
                                    <div class="vault-explorer-search-mode-menu" data-vault-explorer-search-mode-menu role="menu" hidden>
                                        <button type="button" data-vault-explorer-search-mode-option="name" role="menuitemradio" aria-checked="true">Files</button>
                                        <button type="button" data-vault-explorer-search-mode-option="content" role="menuitemradio" aria-checked="false">Contents</button>
                                    </div>
                                </div>
                                <div class="vault-explorer-toolbar-controls" data-vault-explorer-toolbar></div>
                            </div>
                            <div class="vault-explorer-action-panel hidden" data-vault-explorer-action-panel></div>
                        ` : ''}
                        ${options.explorer ? `
                            <div class="vault-explorer-tree-toolbar">
                                <div data-vault-explorer-selection-summary></div>
                                <div data-vault-path-picker-status class="vault-explorer-tree-status text-sm text-txt-secondary">Loading...</div>
                                <div class="vault-explorer-tree-toggles">
                                    <button type="button" class="ui-icon-button is-compact"
                                        data-vault-explorer-tree="expand" aria-label="Expand all folders" title="Expand all folders">${icons.CHEVRONS_DOWN_ICON_SVG}</button>
                                    <button type="button" class="ui-icon-button is-compact"
                                        data-vault-explorer-tree="collapse" aria-label="Collapse all folders" title="Collapse all folders">${icons.CHEVRONS_UP_ICON_SVG}</button>
                                </div>
                            </div>
                        ` : '<div data-vault-path-picker-status class="text-sm text-txt-secondary">Loading...</div>'}
                        <div data-vault-path-picker-results class="workspace-tree flex-1 min-h-0 overflow-y-auto" role="tree"></div>
                    </div>
                </section>
            `;
            document.body.appendChild(overlay);
            syncInteractionLocks();

            const queryInput = overlay.querySelector('[data-vault-path-picker-query]');
            const scopeSelect = overlay.querySelector('[data-vault-path-picker-scope]');
            const searchModeSelect = overlay.querySelector('[data-vault-path-picker-search-mode]');
            const uploadInput = overlay.querySelector('[data-vault-explorer-upload-input]');
            if (scopeSelect instanceof HTMLSelectElement) {
                scopeSelect.value = options.initialScope || (workspacePath() ? 'workspace' : 'vault');
            }
            if (options.explorer) {
                explorer.open(overlay, options, {
                    activeFolder: options.initialScope === 'workspace' ? workspacePath() : '',
                });
                if (options.importUrl) {
                    explorerImports.showUrl(overlay, {
                        destination: options.importOptions?.destination
                            ?? explorer.snapshot().activeFolder,
                        requestOptions: options.importOptions,
                        url: options.importUrl,
                    }, options);
                } else if (Array.isArray(options.importSources) && options.importSources.length) {
                    explorerImports.showFiles(overlay, {
                        destination: options.importOptions?.destination
                            ?? explorer.snapshot().activeFolder,
                        items: options.importSources.map((path) => ({ path, kind: 'file' })),
                        requestOptions: options.importOptions,
                    }, options);
                }
            }

            function syncSearchPlaceholder() {
                if (!(queryInput instanceof HTMLInputElement) || options.searchPlaceholder) return;
                const content = searchModeSelect?.value === 'content';
                const scope = options.explorer
                    ? 'this folder'
                    : scopeSelect?.value === 'vault'
                        ? 'entire vault'
                        : scopeSelect?.value === 'active'
                            ? 'current folder'
                            : 'workspace';
                queryInput.placeholder = content
                    ? `Search contents in ${scope}...`
                    : `Search names in ${scope}...`;
                const modeToggle = overlay.querySelector('[data-vault-explorer-search-mode-toggle]');
                if (modeToggle instanceof HTMLButtonElement) {
                    modeToggle.textContent = content ? 'Contents' : 'Files';
                }
                overlay.querySelectorAll('[data-vault-explorer-search-mode-option]').forEach((button) => {
                    button.setAttribute(
                        'aria-checked',
                        String(button.getAttribute('data-vault-explorer-search-mode-option') === searchModeSelect?.value)
                    );
                });
            }
            syncSearchPlaceholder();

            overlay.addEventListener('click', async (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (event.target === overlay || target.closest('[data-vault-path-picker-close]')) {
                    if (isExplorerBusy()) return;
                    close();
                    return;
                }
                const searchModeOption = target.closest('[data-vault-explorer-search-mode-option]');
                if (searchModeOption instanceof HTMLButtonElement) {
                    if (searchModeSelect instanceof HTMLInputElement) {
                        searchModeSelect.value = searchModeOption.getAttribute(
                            'data-vault-explorer-search-mode-option'
                        ) || 'name';
                    }
                    closeSearchModeMenu(overlay);
                    syncSearchPlaceholder();
                    loadRoot();
                    return;
                }
                const searchModeToggle = target.closest('[data-vault-explorer-search-mode-toggle]');
                if (searchModeToggle instanceof HTMLButtonElement) {
                    toggleSearchModeMenu(overlay, searchModeToggle);
                    return;
                }
                closeSearchModeMenu(overlay);
                const selection = target.closest('[data-vault-explorer-select-item]');
                if (selection instanceof HTMLInputElement) {
                    explorer.toggleSelection({
                        path: selection.dataset.path || '',
                        kind: selection.dataset.kind || '',
                        importEligible: selection.dataset.importEligible === 'true',
                    });
                    return;
                }
                const loadMoreButton = target.closest('[data-vault-path-picker-more]');
                if (loadMoreButton instanceof HTMLButtonElement) {
                    await loadMoreResults(loadMoreButton, options);
                    return;
                }
                if (target.closest('[data-vault-explorer-action-cancel]')) {
                    if (explorerActions.isBusy()) return;
                    explorerActions.closeActionPanel(overlay);
                    return;
                }
                const treeToggle = target.closest('[data-vault-explorer-tree]');
                if (treeToggle instanceof HTMLButtonElement) {
                    const action = treeToggle.getAttribute('data-vault-explorer-tree');
                    if (action === 'expand') {
                        await expandAllFolders(overlay, options, treeToggle);
                    } else if (action === 'collapse') {
                        collapseAllFolders(overlay);
                    }
                    return;
                }
                const toggle = target.closest('[data-vault-path-picker-toggle]');
                if (toggle instanceof HTMLElement) {
                    await toggleNode(overlay, toggle, options);
                    return;
                }
                const selectButton = target.closest('[data-vault-path-picker-select]');
                if (selectButton instanceof HTMLElement) {
                    const path = selectButton.getAttribute('data-vault-path-picker-select') || '';
                    const kind = selectButton.getAttribute('data-vault-path-picker-kind') || '';
                    if (explorerActions.hasDestinationMode()) {
                        if (kind === 'directory') {
                            explorerActions.selectDestination(overlay, path);
                        }
                        return;
                    }
                    if (kind === 'directory' && options.expandDirectoriesOnSelect) {
                        explorer.setActiveFolder(path);
                        const row = selectButton.closest('[data-vault-path-picker-row]');
                        const rowToggle = row?.querySelector(':scope > .workspace-tree-row [data-vault-path-picker-toggle]');
                        if (rowToggle instanceof HTMLElement) {
                            await toggleNode(overlay, rowToggle, options);
                        }
                        return;
                    }
                    if (path || mode === 'directories') {
                        options.onSelect?.({ path, kind });
                        if (options.closeOnSelect !== false) close();
                    }
                }
            });
            overlay.addEventListener('submit', async (event) => {
                const form = event.target;
                if (form instanceof HTMLFormElement && form.matches('[data-vault-explorer-upload-form]')) {
                    event.preventDefault();
                    await explorerActions.submitUploads(overlay, form, options, {
                        importAfterUpload: event.submitter?.value === 'upload_import',
                    });
                    return;
                }
                if (form instanceof HTMLFormElement && form.matches('[data-vault-explorer-import-form]')) {
                    event.preventDefault();
                    await explorerImports.submit(overlay, form, options);
                    return;
                }
                if (
                    form instanceof HTMLFormElement
                    && form.dataset.operation === 'batch_move'
                ) {
                    event.preventDefault();
                    await explorerBatchMoves.submit(overlay, form, options);
                    return;
                }
                if (!(form instanceof HTMLFormElement) || !form.matches('[data-vault-explorer-mutation-form]')) return;
                event.preventDefault();
                await explorerActions.submitMutation(overlay, form, options);
            });
            overlay.addEventListener('input', (event) => {
                const input = event.target;
                if (
                    input instanceof HTMLInputElement
                    && input.matches('[data-vault-explorer-move-name]')
                ) {
                    input.setCustomValidity('');
                    explorerActions.updateMovePreview(overlay);
                }
            });
            overlay.addEventListener('keydown', async (event) => {
                if (!options.explorer) return;
                if (event.key === 'Escape') {
                    const panel = overlay.querySelector('[data-vault-explorer-action-panel]');
                    if (panel instanceof HTMLElement && !panel.classList.contains('hidden')) {
                        event.preventDefault();
                        explorerActions.closeActionPanel(overlay);
                    }
                    return;
                }
                const control = event.target instanceof Element
                    ? event.target.closest(
                        '[data-vault-path-picker-select], [data-vault-path-picker-toggle], [data-vault-explorer-select-item]'
                    )
                    : null;
                if (!(control instanceof HTMLElement)) return;
                const row = control.closest('[data-vault-path-picker-row]');
                if (!(row instanceof HTMLElement)) return;
                if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                    const rows = visibleTreeRows(overlay);
                    const index = rows.indexOf(row);
                    const nextIndex = event.key === 'ArrowDown' ? index + 1 : index - 1;
                    const next = rows[nextIndex]?.querySelector('[data-vault-path-picker-select]');
                    if (next instanceof HTMLElement) {
                        event.preventDefault();
                        next.focus();
                    }
                    return;
                }
                const toggle = row.querySelector(
                    ':scope > .workspace-tree-row [data-vault-path-picker-toggle]'
                );
                if (event.key === 'ArrowRight' && toggle instanceof HTMLElement) {
                    event.preventDefault();
                    if (toggle.getAttribute('aria-expanded') !== 'true') {
                        await toggleNode(overlay, toggle, options);
                    } else {
                        row.querySelector(
                            ':scope > [data-vault-path-picker-children] [data-vault-path-picker-select]'
                        )?.focus();
                    }
                    return;
                }
                if (event.key === 'ArrowLeft') {
                    if (
                        toggle instanceof HTMLElement
                        && toggle.getAttribute('aria-expanded') === 'true'
                    ) {
                        event.preventDefault();
                        await toggleNode(overlay, toggle, options);
                        return;
                    }
                    const parent = row.parentElement?.closest('[data-vault-path-picker-row]');
                    const parentControl = parent?.querySelector(
                        ':scope > .workspace-tree-row [data-vault-path-picker-select]'
                    );
                    if (parentControl instanceof HTMLElement) {
                        event.preventDefault();
                        parentControl.focus();
                    }
                }
            });

            const loadRoot = () => loadCurrentResults(overlay, options).catch((error) => {
                if (error.name !== 'AbortError') {
                    setStatus(overlay, `Unable to load paths: ${error.message}`, true);
                }
            });
            const debouncedLoad = debounce(loadRoot, 180);
            queryInput?.addEventListener('input', debouncedLoad);
            uploadInput?.addEventListener('change', () => {
                explorerActions.setUploadFiles(
                    overlay,
                    uploadInput.files,
                    explorer.snapshot().destinationPath
                );
                uploadInput.value = '';
            });
            scopeSelect?.addEventListener('change', () => {
                syncSearchPlaceholder();
                loadRoot();
            });
            searchModeSelect?.addEventListener('change', () => {
                syncSearchPlaceholder();
                loadRoot();
            });
            const initialPath = options.revealInitialPath
                ? ''
                : (options.initialPath || (options.explorer ? explorer.snapshot().activeFolder : ''));
            loadResults(overlay, options, initialPath)
                .then(() => {
                    if (options.revealInitialPath) {
                        return revealPath(overlay, options, options.revealInitialPath);
                    }
                    return null;
                })
                .catch((error) => {
                    setStatus(overlay, `Unable to load paths: ${error.message}`, true);
                });
        }

        function close() {
            rootAbortController?.abort();
            rootAbortController = null;
            explorerSearch.cancel();
            rootLoadGeneration += 1;
            if (activePickerId) {
                document.getElementById(activePickerId)?.remove();
            }
            document.getElementById('vault-path-picker-modal')?.remove();
            activeOnClose?.();
            activePickerId = '';
            activeOnClose = null;
            activeOptions = null;
            explorer.close();
            explorerImports.reset();
            explorerActions.reset();
        }

        function handleImportAction(action, overlay, snapshot, options) {
            if (action === 'import_url') {
                explorerImports.showUrl(overlay, {
                    destination: snapshot.destinationPath,
                    requestOptions: {},
                }, options);
                return;
            }
            const destination = sharedParent(snapshot.selectedItems)
                ?? snapshot.activeFolder;
            explorerImports.showFiles(overlay, {
                destination,
                items: snapshot.selectedItems,
                requestOptions: {},
            }, options);
        }

        function sharedParent(items) {
            const parents = new Set(items.map((item) => {
                const parts = item.path.split('/');
                parts.pop();
                return parts.join('/');
            }));
            return parents.size === 1 ? Array.from(parents)[0] : null;
        }

        function toggleSearchModeMenu(overlay, toggle) {
            const menu = overlay.querySelector('[data-vault-explorer-search-mode-menu]');
            if (!(menu instanceof HTMLElement)) return;
            const opening = menu.hidden;
            menu.hidden = !opening;
            toggle.setAttribute('aria-expanded', String(opening));
        }

        function closeSearchModeMenu(overlay) {
            const menu = overlay.querySelector('[data-vault-explorer-search-mode-menu]');
            const toggle = overlay.querySelector('[data-vault-explorer-search-mode-toggle]');
            if (menu instanceof HTMLElement) menu.hidden = true;
            toggle?.setAttribute('aria-expanded', 'false');
        }

        function navigateExplorerLocation(path) {
            const overlay = document.getElementById(activePickerId);
            if (!(overlay instanceof HTMLElement) || !activeOptions) return;
            loadCurrentResults(overlay, activeOptions).catch((error) => {
                if (error.name !== 'AbortError') {
                    setStatus(overlay, `Unable to load paths: ${error.message}`, true);
                }
            });
        }

        async function showExplorerSelection(overlay, snapshot, options) {
            const selectedPaths = snapshot.selectedPaths || [];
            if (!selectedPaths.length) return;
            const query = overlay.querySelector('[data-vault-path-picker-query]');
            const searchMode = overlay.querySelector('[data-vault-path-picker-search-mode]');
            if (query instanceof HTMLInputElement) {
                query.value = '';
                if (!options.searchPlaceholder) {
                    query.placeholder = 'Search names in this folder...';
                }
            }
            if (searchMode instanceof HTMLInputElement) searchMode.value = 'name';
            const modeToggle = overlay.querySelector('[data-vault-explorer-search-mode-toggle]');
            if (modeToggle instanceof HTMLButtonElement) modeToggle.textContent = 'Files';
            closeSearchModeMenu(overlay);
            overlay.querySelectorAll('[data-vault-explorer-search-mode-option]').forEach((button) => {
                button.setAttribute(
                    'aria-checked',
                    String(button.getAttribute('data-vault-explorer-search-mode-option') === 'name')
                );
            });
            explorer.setActiveFolder('');
            await loadResults(overlay, options, '');
            for (const path of selectedPaths) {
                await revealPath(overlay, options, path, { scroll: false });
            }
            const firstSelected = Array.from(
                overlay.querySelectorAll('[data-vault-path-picker-row]')
            ).find((row) => selectedPaths.includes(
                row.getAttribute('data-vault-path-picker-row') || ''
            ));
            firstSelected?.scrollIntoView({ block: 'nearest' });
        }

        async function expandDirectory(overlay, path, options) {
            const row = Array.from(
                overlay.querySelectorAll('[data-vault-path-picker-row]')
            ).find((candidate) => (
                candidate instanceof HTMLElement
                && candidate.getAttribute('data-vault-path-picker-row') === path
            ));
            const toggle = row?.querySelector(
                ':scope > .workspace-tree-row [data-vault-path-picker-toggle]'
            );
            if (
                toggle instanceof HTMLElement
                && toggle.getAttribute('aria-expanded') !== 'true'
            ) {
                await toggleNode(overlay, toggle, options);
            }
        }

        async function expandAllFolders(overlay, options, button) {
            button.disabled = true;
            try {
                while (true) {
                    const collapsed = Array.from(
                        overlay.querySelectorAll('[data-vault-path-picker-toggle][aria-expanded="false"]')
                    );
                    if (!collapsed.length) return;
                    for (const toggle of collapsed) {
                        if (
                            toggle instanceof HTMLElement
                            && toggle.isConnected
                            && toggle.getAttribute('aria-expanded') === 'false'
                        ) {
                            await toggleNode(overlay, toggle, options);
                        }
                    }
                }
            } finally {
                button.disabled = false;
            }
        }

        function collapseAllFolders(overlay) {
            overlay.querySelectorAll('[data-vault-path-picker-toggle][aria-expanded="true"]')
                .forEach((toggle) => toggle.setAttribute('aria-expanded', 'false'));
            overlay.querySelectorAll('[data-vault-path-picker-children]')
                .forEach((children) => children.classList.add('hidden'));
        }

        function syncInteractionLocks() {
            if (!activePickerId || !activeOptions) return;
            const overlay = document.getElementById(activePickerId);
            if (!(overlay instanceof HTMLElement)) return;
            const readOnly = isReadOnly(activeOptions);
            overlay.querySelectorAll('[data-vault-explorer-mutation-form] button[type="submit"]').forEach((button) => {
                if (button instanceof HTMLButtonElement) button.disabled = readOnly;
            });
            explorer.render();
            if (readOnly) {
                explorerActions.closeActionPanel(overlay);
            }
        }

        async function loadResults(overlay, options, path = '') {
            const generation = ++rootLoadGeneration;
            rootAbortController?.abort();
            const controller = new AbortController();
            rootAbortController = controller;
            const mode = options.mode === 'directories' ? 'directories' : 'files';
            setStatus(overlay, 'Loading...');
            const results = overlay.querySelector('[data-vault-path-picker-results]');
            if (!(results instanceof HTMLElement)) return;
            results.innerHTML = '';
            if (mode === 'directories') {
                const payload = await fetchDirectories(path, controller.signal, options);
                if (generation !== rootLoadGeneration || !overlay.isConnected) return;
                const items = Array.isArray(payload.directories)
                    ? payload.directories.map((item) => ({ ...item, kind: 'directory' }))
                    : [];
                setStatus(overlay, items.length ? `Showing ${items.length} folder${items.length === 1 ? '' : 's'}.` : 'No folders available.');
                results.innerHTML = items.length
                    ? items.map((item) => renderRow(item, 0, options)).join('')
                    : `<p class="text-sm text-txt-secondary">${escapeHtml(options.emptyText || 'No folders available.')}</p>`;
                explorer.render();
                return;
            }

            const queryInput = overlay.querySelector('[data-vault-path-picker-query]');
            const scopeSelect = overlay.querySelector('[data-vault-path-picker-scope]');
            const selectedScope = scopeSelect instanceof HTMLSelectElement
                ? scopeSelect.value
                : options.explorer ? 'vault' : 'workspace';
            const payload = await fetchFileRefs({
                path: path || (options.explorer
                    ? explorer.snapshot().activeFolder
                    : selectedScope === 'active' ? explorer.snapshot().activeFolder : ''),
                query: queryInput instanceof HTMLInputElement ? queryInput.value.trim() : '',
                scope: selectedScope === 'active' ? 'vault' : selectedScope,
                signal: controller.signal,
                options,
            });
            if (generation !== rootLoadGeneration || !overlay.isConnected) return;
            const items = Array.isArray(payload.items) ? payload.items : [];
            setStatus(overlay, renderFileStatus(payload, items.length));
            results.innerHTML = items.length
                ? items.map((item) => renderRow(item, 0, options)).join('') + renderLoadMore(payload, 0, options)
                : `<p class="text-sm text-txt-secondary">${escapeHtml(options.emptyText || 'No matching files.')}</p>`;
            explorer.render();
        }

        function loadCurrentResults(overlay, options) {
            const searchMode = overlay.querySelector('[data-vault-path-picker-search-mode]');
            if (
                (searchMode instanceof HTMLSelectElement || searchMode instanceof HTMLInputElement)
                && searchMode.value === 'content'
            ) {
                return loadContentResults(overlay, options);
            }
            explorerSearch.cancel();
            return loadResults(overlay, options);
        }

        function searchScopePath(overlay) {
            if (activeOptions?.explorer) return explorer.snapshot().activeFolder;
            const scope = overlay.querySelector('[data-vault-path-picker-scope]');
            if (!(scope instanceof HTMLSelectElement)) return workspacePath();
            if (scope.value === 'vault') return '';
            if (scope.value === 'active') return explorer.snapshot().activeFolder;
            return workspacePath();
        }

        async function loadContentResults(overlay, options) {
            rootAbortController?.abort();
            rootAbortController = null;
            const queryInput = overlay.querySelector('[data-vault-path-picker-query]');
            const query = queryInput instanceof HTMLInputElement ? queryInput.value.trim() : '';
            await explorerSearch.load({
                overlay,
                vault: options.vaultName || selectedVault(),
                query,
                path: searchScopePath(overlay),
                selectedPaths: explorer.snapshot().selectedPaths,
                supportsPath: window.VaultExplorerImports.supportsPath,
                setStatus: (message) => setStatus(overlay, message),
                onRendered: explorer.render,
            });
        }

        async function fetchDirectories(path, signal = undefined, options = {}) {
            const params = new URLSearchParams();
            if (path) params.set('path', path);
            const suffix = params.toString() ? `?${params.toString()}` : '';
            const vault = options.vaultName || selectedVault();
            const response = await fetch(`api/vaults/${encodeURIComponent(vault)}/directories${suffix}`, { signal });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            return response.json();
        }

        async function fetchFileRefs({ path = '', query = '', scope = 'workspace', offset = 0, signal = undefined, options = {} } = {}) {
            const params = new URLSearchParams();
            if (path) params.set('path', path);
            if (scope === 'workspace' && workspacePath()) {
                params.set('workspace_path', workspacePath());
            }
            if (query) params.set('query', query);
            if (offset) params.set('offset', String(offset));
            params.set('scope', scope || 'workspace');
            const vault = options.vaultName || selectedVault();
            const response = await fetch(`api/vaults/${encodeURIComponent(vault)}/file-refs?${params.toString()}`, { signal });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            return response.json();
        }

        async function toggleNode(overlay, toggle, options) {
            const row = toggle.closest('[data-vault-path-picker-row]');
            if (!(row instanceof HTMLElement)) return;
            const children = row.querySelector(':scope > [data-vault-path-picker-children]');
            if (!(children instanceof HTMLElement)) return;
            const expanded = toggle.getAttribute('aria-expanded') === 'true';
            const treeItem = row.querySelector(':scope > .workspace-tree-row');
            if (expanded) {
                toggle.setAttribute('aria-expanded', 'false');
                treeItem?.setAttribute('aria-expanded', 'false');
                children.classList.add('hidden');
                return;
            }
            toggle.setAttribute('aria-expanded', 'true');
            treeItem?.setAttribute('aria-expanded', 'true');
            children.classList.remove('hidden');
            if (children.dataset.loaded === 'true') return;

            const path = row.getAttribute('data-vault-path-picker-row') || '';
            children.innerHTML = '<div class="py-1 text-xs text-txt-secondary">Loading...</div>';
            try {
                const mode = options.mode === 'directories' ? 'directories' : 'files';
                const depth = Number.parseInt(row.getAttribute('data-vault-path-picker-depth') || '0', 10) + 1;
                if (mode === 'directories') {
                    const payload = await fetchDirectories(path, undefined, options);
                    const items = Array.isArray(payload.directories)
                        ? payload.directories.map((item) => ({ ...item, kind: 'directory' }))
                        : [];
                    children.innerHTML = items.length
                        ? items.map((item) => renderRow(item, depth, options)).join('')
                        : '<div class="py-1 text-xs text-txt-secondary">No child folders.</div>';
                } else {
                    const payload = await fetchFileRefs({ path, scope: 'vault', options });
                    const items = Array.isArray(payload.items) ? payload.items : [];
                    children.innerHTML = items.length
                        ? items.map((item) => renderRow(item, depth, options)).join('') + renderLoadMore(payload, depth, options)
                        : '<div class="py-1 text-xs text-txt-secondary">No child files.</div>';
                }
                children.dataset.loaded = 'true';
                explorerActions.syncDestinationSelection(overlay);
                explorer.render();
            } catch (error) {
                children.innerHTML = `<div class="py-1 text-xs state-error">Unable to load paths: ${escapeHtml(error.message)}</div>`;
            }
        }

        async function revealPath(overlay, options, path, { scroll = true } = {}) {
            const segments = String(path || '').split('/').filter(Boolean);
            let currentPath = '';
            let revealedRow = null;
            for (const segment of segments) {
                currentPath = currentPath ? `${currentPath}/${segment}` : segment;
                let row = findTreeRow(overlay, currentPath);
                while (!(row instanceof HTMLElement)) {
                    const parentPath = currentPath.split('/').slice(0, -1).join('/');
                    const parent = parentPath ? findTreeRow(overlay, parentPath) : null;
                    const pageRoot = parent?.querySelector(
                        ':scope > [data-vault-path-picker-children]'
                    ) || overlay.querySelector('[data-vault-path-picker-results]');
                    const more = pageRoot?.querySelector('[data-vault-path-picker-more]');
                    if (!(more instanceof HTMLButtonElement)) break;
                    await loadMoreResults(more, options);
                    row = findTreeRow(overlay, currentPath);
                }
                if (!(row instanceof HTMLElement)) return;
                revealedRow = row;
                const toggle = row.querySelector(
                    ':scope > .workspace-tree-row [data-vault-path-picker-toggle]'
                );
                if (
                    toggle instanceof HTMLElement
                    && toggle.getAttribute('aria-expanded') !== 'true'
                ) {
                    await toggleNode(overlay, toggle, options);
                }
            }
            if (scroll) revealedRow?.scrollIntoView({ block: 'nearest' });
        }

        function findTreeRow(overlay, path) {
            return Array.from(overlay.querySelectorAll('[data-vault-path-picker-row]'))
                .find((candidate) => (
                    candidate instanceof HTMLElement
                    && candidate.getAttribute('data-vault-path-picker-row') === path
                )) || null;
        }

        function visibleTreeRows(overlay) {
            return Array.from(overlay.querySelectorAll('[data-vault-path-picker-row]'))
                .filter((row) => row instanceof HTMLElement && row.offsetParent !== null);
        }

        function renderRow(item, depth, options) {
            const path = String(item.path || '');
            const name = String(item.name || path || 'Path');
            const kind = item.kind === 'directory' ? 'directory' : 'file';
            const indent = Math.min(Math.max(depth, 0) * 1.25, 5);
            const canExpand = kind === 'directory' && item.has_children;
            const icon = kind === 'directory' ? icons.FOLDER_ICON_SVG : fileIcon();
            const selected = options.explorer
                && explorer.snapshot().selectedPaths.includes(path);
            const active = options.explorer
                && kind === 'directory'
                && explorer.snapshot().activeFolder === path;
            return `
                <div data-vault-path-picker-row="${escapeHtml(path)}" data-vault-path-picker-depth="${depth}">
                    <div class="workspace-tree-row${selected ? ' is-selected' : ''}${active ? ' is-active-folder' : ''}" role="treeitem" aria-level="${depth + 1}" aria-selected="${selected ? 'true' : 'false'}"${canExpand ? ' aria-expanded="false"' : ''} style="padding-left: ${indent}rem;">
                        ${canExpand
                            ? `<button type="button" class="workspace-tree-toggle" data-vault-path-picker-toggle aria-expanded="false" aria-label="Expand ${escapeHtml(name)}">
                                <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                                    <path d="M7.25 4.75 12.75 10l-5.5 5.25" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
                                </svg>
                            </button>`
                            : '<span class="workspace-tree-spacer" aria-hidden="true"></span>'}
                        ${options.explorer ? `
                            <input type="checkbox" class="vault-explorer-row-selection"
                                data-vault-explorer-select-item data-path="${escapeHtml(path)}"
                                data-kind="${kind}" data-import-eligible="${kind === 'file' && window.VaultExplorerImports.supportsPath(path)}"
                                aria-label="Select ${escapeHtml(name)}" ${selected ? 'checked' : ''} />
                        ` : ''}
                        <button type="button" class="workspace-tree-select" data-vault-path-picker-select="${escapeHtml(path)}" data-vault-path-picker-kind="${escapeHtml(kind)}">
                            <span class="file-reference-row-icon" aria-hidden="true">${icon}</span>
                            <span class="workspace-tree-label min-w-0">
                                <span class="workspace-tree-name">${escapeHtml(name)}</span>
                                ${options.showPath === false ? '' : `<span class="file-reference-path">${escapeHtml(path)}</span>`}
                            </span>
                        </button>
                        ${options.explorer && kind === 'directory'
                            ? '<span class="vault-explorer-descendant-selection" data-vault-explorer-descendant-selection hidden></span>'
                            : ''}
                    </div>
                    <div class="workspace-tree-children hidden" data-vault-path-picker-children></div>
                </div>
            `;
        }

        function fileIcon() {
            return icons.FILE_TEXT_ICON_SVG || `
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                    <path d="M14 2v4a2 2 0 0 0 2 2h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                </svg>
            `;
        }

        function renderFileStatus(payload, count) {
            const scope = payload?.scope === 'vault' ? 'vault' : 'workspace';
            if (!payload?.query) return '';
            const base = `Found ${count}`;
            const root = payload?.path || (scope === 'workspace' ? workspacePath() : '') || 'vault root';
            const suffix = payload?.truncated && payload?.next_offset == null
                ? ' Refine the search to see more.'
                : '';
            return `${base} item${count === 1 ? '' : 's'} in ${scope}: ${root}.${suffix}`;
        }

        function renderLoadMore(payload, depth, options) {
            if (!Number.isInteger(payload?.next_offset)) return '';
            return `
                <button type="button" class="vault-path-picker-more" data-vault-path-picker-more
                    data-path="${escapeHtml(payload.path || '')}"
                    data-offset="${payload.next_offset}"
                    data-depth="${depth}">Load more</button>
            `;
        }

        async function loadMoreResults(button, options) {
            button.disabled = true;
            const path = button.dataset.path || '';
            const offset = Number.parseInt(button.dataset.offset || '0', 10);
            const depth = Number.parseInt(button.dataset.depth || '0', 10);
            try {
                const payload = await fetchFileRefs({ path, scope: 'vault', offset, options });
                const items = Array.isArray(payload.items) ? payload.items : [];
                button.insertAdjacentHTML(
                    'beforebegin',
                    items.map((item) => renderRow(item, depth, options)).join('')
                    + renderLoadMore(payload, depth, options)
                );
                button.remove();
            } catch (error) {
                button.disabled = false;
                button.textContent = `Retry: ${error.message}`;
            }
        }

        function setStatus(overlay, message, error = false) {
            const status = overlay.querySelector('[data-vault-path-picker-status]');
            if (!status) return;
            status.innerHTML = error ? `<span class="state-error">${escapeHtml(message)}</span>` : escapeHtml(message);
        }

        async function refreshExplorer(overlay, options, reveal = '') {
            const query = overlay.querySelector('[data-vault-path-picker-query]');
            const scope = overlay.querySelector('[data-vault-path-picker-scope]');
            if (query instanceof HTMLInputElement) query.value = '';
            if (scope instanceof HTMLSelectElement) scope.value = 'vault';
            await loadResults(
                overlay,
                options,
                options.explorer ? explorer.snapshot().activeFolder : ''
            );
            if (reveal) await revealPath(overlay, options, reveal);
        }

        function debounce(fn, delayMs) {
            let timer = null;
            return (...args) => {
                if (timer) window.clearTimeout(timer);
                timer = window.setTimeout(() => fn(...args), delayMs);
            };
        }

        return Object.freeze({ open, close, syncInteractionLocks });
    }

    window.VaultPathPicker = Object.freeze({
        create: createVaultPathPickerController,
    });
})(window, document);
