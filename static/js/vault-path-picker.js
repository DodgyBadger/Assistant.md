(function vaultPathPickerModule(window, document) {
    function createVaultPathPickerController({ elements, icons, utils }) {
        const { escapeHtml } = utils;
        let activePickerId = '';
        let activeOnClose = null;
        let activeOptions = null;
        let rootLoadGeneration = 0;
        let rootAbortController = null;
        const explorerActions = window.VaultExplorerActions.create({
            icons,
            utils,
            callbacks: {
                isReadOnly,
                mutationCompleted,
                refreshExplorer,
                setStatus,
                syncInteractionLocks,
                workspacePath,
            },
        });
        const explorerImports = window.VaultExplorerImports.create({
            utils,
            callbacks: {
                closeActionPanel: explorerActions.closeActionPanel,
                refreshExplorer,
                syncInteractionLocks,
            },
        });
        const explorer = window.VaultExplorerController.create({
            utils,
            callbacks: {
                expandDirectory,
                handleImportAction,
                handleMutationAction: explorerActions.handleAction,
                isBusy: isExplorerBusy,
                isReadOnly,
                refreshExplorer,
                setStatus,
                supportsImportPath: window.VaultExplorerImports.supportsPath,
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
                <section class="app-modal-panel relative flex flex-col" role="dialog" aria-modal="true" aria-labelledby="${escapeHtml(titleId)}">
                    <div class="app-modal-header flex-none">
                        <div class="app-modal-title-block">
                            <h2 id="${escapeHtml(titleId)}" class="text-lg font-semibold text-txt-primary">${escapeHtml(options.title || 'Choose Path')}</h2>
                            <p class="mt-1 text-xs text-txt-secondary cell-mono">${escapeHtml(options.subtitle || vault)}</p>
                        </div>
                        <div class="app-modal-actions">
                            <button type="button" class="ui-icon-button is-compact" data-vault-path-picker-close aria-label="Close" title="Close">${icons.X_ICON_SVG}</button>
                        </div>
                    </div>
                    <div class="p-4 flex-1 min-h-0 flex flex-col gap-3">
                        ${options.selectedLabel ? `
                            <div class="p-3 rounded border border-border-primary bg-app-elevated">
                                <div class="text-xs uppercase text-txt-secondary">${escapeHtml(options.selectedLabel)}</div>
                                <div class="mt-1 text-sm cell-mono text-txt-primary">${escapeHtml(options.selectedPath || 'None')}</div>
                                ${options.workspaceSelectionMode
                                    ? `<div class="mt-2 text-xs text-txt-secondary">Choose <strong>Use</strong> beside a folder to ${options.workspaceRecovery ? 'replace it' : 'set a new workspace'}.</div>`
                                    : ''}
                            </div>
                        ` : ''}
                        ${showSearch ? `
                            <div class="file-reference-toolbar">
                                <select data-vault-path-picker-scope class="file-reference-scope" aria-label="Search scope">
                                    <option value="workspace">Workspace only</option>
                                    <option value="vault">Entire vault</option>
                                </select>
                                <input data-vault-path-picker-query type="search" class="file-reference-search" placeholder="${escapeHtml(options.searchPlaceholder || 'Search workspace...')}" aria-label="Search files" />
                            </div>
                        ` : ''}
                        ${options.explorer ? `
                            <input type="file" class="hidden" data-vault-explorer-upload-input multiple />
                            <div class="vault-explorer-toolbar" data-vault-explorer-toolbar></div>
                            <div class="vault-explorer-action-panel hidden" data-vault-explorer-action-panel></div>
                        ` : ''}
                        <div data-vault-path-picker-status class="text-sm text-txt-secondary">Loading...</div>
                        <div data-vault-path-picker-results class="workspace-tree flex-1 min-h-0 overflow-y-auto" role="tree"></div>
                    </div>
                </section>
            `;
            document.body.appendChild(overlay);
            syncInteractionLocks();

            const queryInput = overlay.querySelector('[data-vault-path-picker-query]');
            const scopeSelect = overlay.querySelector('[data-vault-path-picker-scope]');
            const uploadInput = overlay.querySelector('[data-vault-explorer-upload-input]');
            if (scopeSelect instanceof HTMLSelectElement) {
                scopeSelect.value = options.initialScope || (workspacePath() ? 'workspace' : 'vault');
            }
            if (options.explorer) {
                explorer.open(overlay, options, {
                    activeFolder: scopeSelect?.value === 'workspace' ? workspacePath() : '',
                });
            }

            function syncSearchPlaceholder() {
                if (!(queryInput instanceof HTMLInputElement) || options.searchPlaceholder) return;
                queryInput.placeholder = scopeSelect?.value === 'vault'
                    ? 'Search entire vault...'
                    : 'Search workspace...';
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

            const loadRoot = () => loadResults(overlay, options).catch((error) => {
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
                if (options.explorer) {
                    explorer.setActiveFolder(
                        scopeSelect.value === 'workspace' ? workspacePath() : ''
                    );
                }
                syncSearchPlaceholder();
                loadRoot();
            });
            const initialPath = options.revealInitialPath ? '' : (options.initialPath || '');
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
                }, options);
                return;
            }
            const destination = sharedParent(snapshot.selectedItems)
                ?? snapshot.activeFolder;
            explorerImports.showFiles(overlay, {
                destination,
                items: snapshot.selectedItems,
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
            const payload = await fetchFileRefs({
                path,
                query: queryInput instanceof HTMLInputElement ? queryInput.value.trim() : '',
                scope: scopeSelect instanceof HTMLSelectElement ? scopeSelect.value : 'workspace',
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
            if (expanded) {
                toggle.setAttribute('aria-expanded', 'false');
                children.classList.add('hidden');
                return;
            }
            toggle.setAttribute('aria-expanded', 'true');
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

        async function revealPath(overlay, options, path) {
            const segments = String(path || '').split('/').filter(Boolean);
            let currentPath = '';
            let revealedRow = null;
            for (const segment of segments) {
                currentPath = currentPath ? `${currentPath}/${segment}` : segment;
                const row = Array.from(
                    overlay.querySelectorAll('[data-vault-path-picker-row]')
                ).find((candidate) => (
                    candidate instanceof HTMLElement
                    && candidate.getAttribute('data-vault-path-picker-row') === currentPath
                ));
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
            revealedRow?.scrollIntoView({ block: 'nearest' });
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
                    <div class="workspace-tree-row${selected ? ' is-selected' : ''}${active ? ' is-active-folder' : ''}" role="treeitem" style="padding-left: ${indent}rem;">
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
            const base = payload?.query ? `Found ${count}` : `Showing ${count}`;
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
            await loadResults(overlay, options);
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
