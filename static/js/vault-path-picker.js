(function vaultPathPickerModule(window, document) {
    function createVaultPathPickerController({ elements, icons, utils }) {
        const { escapeHtml, flashCopyFeedback, handleCopy } = utils;
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
                refreshExplorer,
                setStatus,
                syncInteractionLocks,
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
                            ${options.explorer ? `
                                <form class="vault-explorer-header-create hidden" data-vault-explorer-header-create data-direct-path="true">
                                    <label class="vault-explorer-visually-hidden" data-vault-explorer-header-create-label for="${escapeHtml(id)}-create-path">New path</label>
                                    <input id="${escapeHtml(id)}-create-path" name="value" class="vault-explorer-header-create-input" autocomplete="off" required />
                                    <button type="submit" class="ui-icon-button is-primary is-compact" aria-label="Create" title="Create">${icons.CHECK_ICON_SVG}</button>
                                    <button type="button" class="ui-icon-button is-compact" data-vault-explorer-header-create-cancel aria-label="Cancel creation" title="Cancel creation">${icons.X_ICON_SVG}</button>
                                </form>
                                <div class="vault-explorer-header-new">
                                    <button type="button" class="ui-icon-button is-compact" data-vault-explorer-new aria-label="Create file or folder" title="Create file or folder">${icons.PLUS_ICON_SVG}</button>
                                    <div class="vault-explorer-header-new-menu hidden" data-vault-explorer-new-menu>
                                        <button type="button" data-vault-explorer-create-kind="file">New file</button>
                                        <button type="button" data-vault-explorer-create-kind="directory">New folder</button>
                                    </div>
                                </div>
                                <input type="file" class="hidden" data-vault-explorer-upload-input multiple />
                                <button type="button" class="ui-icon-button is-compact" data-vault-explorer-upload aria-label="Upload files" title="Upload files">${icons.IMPORT_ICON_SVG}</button>
                                <button type="button" class="ui-icon-button is-compact" data-vault-explorer-refresh aria-label="Refresh vault" title="Refresh vault">${icons.REFRESH_ICON_SVG}</button>
                            ` : ''}
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
                        ${options.explorer ? '<div class="vault-explorer-action-panel hidden" data-vault-explorer-action-panel></div>' : ''}
                        <div data-vault-path-picker-status class="text-sm text-txt-secondary">Loading...</div>
                        <div data-vault-path-picker-results class="workspace-tree flex-1 min-h-0 overflow-y-auto" role="tree"></div>
                    </div>
                </section>
            `;
            document.body.appendChild(overlay);
            syncInteractionLocks();

            const queryInput = overlay.querySelector('[data-vault-path-picker-query]');
            const scopeSelect = overlay.querySelector('[data-vault-path-picker-scope]');
            const headerCreateInput = overlay.querySelector('.vault-explorer-header-create-input');
            const uploadInput = overlay.querySelector('[data-vault-explorer-upload-input]');
            if (scopeSelect instanceof HTMLSelectElement) {
                scopeSelect.value = options.initialScope || (workspacePath() ? 'workspace' : 'vault');
            }
            headerCreateInput?.addEventListener('input', () => {
                headerCreateInput.setCustomValidity('');
            });

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
                if (!target.closest('.vault-explorer-header-new')) {
                    overlay.querySelector('[data-vault-explorer-new-menu]')?.classList.add('hidden');
                }
                if (event.target === overlay || target.closest('[data-vault-path-picker-close]')) {
                    if (explorerActions.isBusy()) return;
                    close();
                    return;
                }
                if (target.closest('[data-vault-explorer-header-create-cancel]')) {
                    explorerActions.closeHeaderCreateForm(overlay);
                    return;
                }
                if (target.closest('[data-vault-explorer-new]')) {
                    explorerActions.toggleHeaderCreateMenu(overlay);
                    return;
                }
                if (target.closest('[data-vault-explorer-upload]')) {
                    if (!isReadOnly(options) && uploadInput instanceof HTMLInputElement) {
                        uploadInput.click();
                    }
                    return;
                }
                const createKindButton = target.closest('[data-vault-explorer-create-kind]');
                if (createKindButton instanceof HTMLButtonElement) {
                    if (isReadOnly(options)) return;
                    explorerActions.showHeaderCreateForm(
                        overlay,
                        createKindButton.dataset.vaultExplorerCreateKind || 'file'
                    );
                    return;
                }
                if (target.closest('[data-vault-explorer-refresh]')) {
                    try {
                        await refreshExplorer(overlay, options);
                    } catch (error) {
                        setStatus(overlay, `Unable to refresh paths: ${error.message}`, true);
                    }
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
                const copyButton = target.closest('[data-vault-explorer-copy]');
                if (copyButton instanceof HTMLButtonElement) {
                    const path = copyButton.getAttribute('data-vault-explorer-copy') || '';
                    flashCopyFeedback(copyButton, await handleCopy(path));
                    return;
                }
                const moreButton = target.closest('[data-vault-explorer-more]');
                if (moreButton instanceof HTMLButtonElement) {
                    explorerActions.toggleRowMenu(overlay, moreButton);
                    return;
                }
                const rowAction = target.closest('[data-vault-explorer-row-action]');
                if (rowAction instanceof HTMLButtonElement) {
                    if (isReadOnly(options)) return;
                    await explorerActions.handleRowAction(overlay, rowAction, options);
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
                    if (explorerActions.hasMoveForm(overlay)) {
                        if (kind === 'directory') {
                            explorerActions.selectMoveDestination(overlay, path);
                        }
                        return;
                    }
                    if (kind === 'directory' && options.expandDirectoriesOnSelect) {
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
                if (form instanceof HTMLFormElement && form.matches('[data-vault-explorer-header-create]')) {
                    event.preventDefault();
                    await explorerActions.submitMutation(overlay, form, options);
                    return;
                }
                if (form instanceof HTMLFormElement && form.matches('[data-vault-explorer-upload-form]')) {
                    event.preventDefault();
                    await explorerActions.submitUploads(overlay, form, options);
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
                explorerActions.setUploadFiles(overlay, uploadInput.files);
                uploadInput.value = '';
            });
            scopeSelect?.addEventListener('change', () => {
                explorerActions.closeHeaderCreateForm(overlay);
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
            explorerActions.reset();
        }

        function syncInteractionLocks() {
            if (!activePickerId || !activeOptions) return;
            const overlay = document.getElementById(activePickerId);
            if (!(overlay instanceof HTMLElement)) return;
            const readOnly = isReadOnly(activeOptions);
            const lockMessage = 'Available when the active response finishes.';
            overlay.querySelectorAll('[data-vault-explorer-more]').forEach((button) => {
                if (!(button instanceof HTMLButtonElement)) return;
                button.disabled = readOnly;
                button.title = readOnly ? lockMessage : 'More actions';
            });
            overlay.querySelectorAll('[data-vault-explorer-mutation-form] button[type="submit"]').forEach((button) => {
                if (button instanceof HTMLButtonElement) button.disabled = readOnly;
            });
            const newButton = overlay.querySelector('[data-vault-explorer-new]');
            if (newButton instanceof HTMLButtonElement) {
                newButton.disabled = readOnly;
                newButton.title = readOnly ? lockMessage : 'Create file or folder';
            }
            const uploadButton = overlay.querySelector('[data-vault-explorer-upload]');
            if (uploadButton instanceof HTMLButtonElement) {
                uploadButton.disabled = readOnly;
                uploadButton.title = readOnly ? lockMessage : 'Upload files';
            }
            if (readOnly) {
                explorerActions.closeActionPanel(overlay);
                explorerActions.closeHeaderCreateForm(overlay);
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
                explorerActions.syncMoveDestinationSelection(overlay);
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
            const readOnly = options.explorer && isReadOnly(options);
            const moreTitle = readOnly ? 'Available when the active response finishes.' : 'More actions';
            return `
                <div data-vault-path-picker-row="${escapeHtml(path)}" data-vault-path-picker-depth="${depth}">
                    <div class="workspace-tree-row" role="treeitem" style="padding-left: ${indent}rem;">
                        ${canExpand
                            ? `<button type="button" class="workspace-tree-toggle" data-vault-path-picker-toggle aria-expanded="false" aria-label="Expand ${escapeHtml(name)}">
                                <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                                    <path d="M7.25 4.75 12.75 10l-5.5 5.25" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
                                </svg>
                            </button>`
                            : '<span class="workspace-tree-spacer" aria-hidden="true"></span>'}
                        <button type="button" class="workspace-tree-select" data-vault-path-picker-select="${escapeHtml(path)}" data-vault-path-picker-kind="${escapeHtml(kind)}">
                            <span class="file-reference-row-icon" aria-hidden="true">${icon}</span>
                            <span class="workspace-tree-label min-w-0">
                                <span class="workspace-tree-name">${escapeHtml(name)}</span>
                                ${options.showPath === false ? '' : `<span class="file-reference-path">${escapeHtml(path)}</span>`}
                            </span>
                        </button>
                        ${options.explorer ? `
                            <div class="vault-explorer-row-actions">
                                ${options.workspaceSelectionMode && kind === 'directory'
                                    ? `<button type="button" class="vault-explorer-workspace-use" data-vault-explorer-row-action="workspace" data-path="${escapeHtml(path)}" data-kind="${kind}" aria-label="Use ${escapeHtml(path)} as the workspace" title="Use as workspace" ${readOnly ? 'disabled' : ''}>Use</button>`
                                    : ''}
                                <button type="button" class="ui-icon-button is-compact" data-vault-explorer-copy="${escapeHtml(path)}" aria-label="Copy path" title="Copy path">${icons.COPY_ICON_SVG}</button>
                                <button type="button" class="ui-icon-button is-compact" data-vault-explorer-more="${escapeHtml(path)}" aria-label="More actions" title="${moreTitle}" ${readOnly ? 'disabled' : ''}>${icons.MORE_HORIZONTAL_ICON_SVG}</button>
                                <div class="vault-explorer-row-menu hidden" data-vault-explorer-row-menu>
                                    <button type="button" data-vault-explorer-row-action="reference" data-path="${escapeHtml(path)}" data-kind="${kind}">Add to prompt</button>
                                    ${kind === 'directory' ? `<button type="button" data-vault-explorer-row-action="workspace" data-path="${escapeHtml(path)}" data-kind="${kind}">Set as workspace</button>` : ''}
                                    ${kind === 'directory' ? `<button type="button" data-vault-explorer-row-action="create_file" data-path="${escapeHtml(path)}" data-kind="${kind}">Create file</button>` : ''}
                                    ${kind === 'directory' ? `<button type="button" data-vault-explorer-row-action="create_directory" data-path="${escapeHtml(path)}" data-kind="${kind}">Create folder</button>` : ''}
                                    <button type="button" data-vault-explorer-row-action="rename" data-path="${escapeHtml(path)}" data-kind="${kind}">Rename</button>
                                    <button type="button" data-vault-explorer-row-action="move" data-path="${escapeHtml(path)}" data-kind="${kind}">Move</button>
                                    <button type="button" class="state-error" data-vault-explorer-row-action="delete" data-path="${escapeHtml(path)}" data-kind="${kind}">Delete</button>
                                </div>
                            </div>
                        ` : ''}
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
