(function vaultExplorerActionsModule(window) {
    function createVaultExplorerActionsController({ icons, utils, callbacks }) {
        const { escapeHtml } = utils;
        let activeUploadFiles = [];
        let activeUploadDestination = '';
        let activeImportDestination = '';
        let uploadInProgress = false;
        const destinationMode = window.VaultExplorerDestination.create();

        function workspacePath() {
            return callbacks.workspacePath();
        }

        function isReadOnly(options) {
            return callbacks.isReadOnly(options);
        }

        function actionPanel(overlay) {
            return overlay.querySelector('[data-vault-explorer-action-panel]');
        }

        function reset() {
            activeUploadFiles = [];
            activeUploadDestination = '';
            activeImportDestination = '';
            uploadInProgress = false;
            destinationMode.cancel();
        }

        function isBusy() {
            return uploadInProgress;
        }

        function closeActionPanel(overlay, { restoreFocus = true } = {}) {
            const panel = actionPanel(overlay);
            if (!(panel instanceof HTMLElement)) return;
            const wasUpload = Boolean(
                panel.querySelector('[data-vault-explorer-upload-form]')
            );
            panel.classList.add('hidden');
            panel.innerHTML = '';
            destinationMode.cancel();
            overlay.classList.remove('vault-explorer-choosing-destination');
            overlay.classList.remove('vault-explorer-preparing-upload');
            syncDestinationSelection(overlay);
            if (wasUpload) {
                activeUploadFiles = [];
                activeUploadDestination = '';
                activeImportDestination = '';
            }
            if (restoreFocus) {
                overlay.querySelector(
                    '[data-vault-explorer-toolbar] button:not([disabled])'
                )?.focus();
            }
        }

        function setUploadFiles(overlay, files, destination = '') {
            activeUploadFiles = Array.from(files || []);
            activeUploadDestination = String(destination || '');
            activeImportDestination = activeUploadDestination;
            if (activeUploadFiles.length) showUploadForm(overlay);
        }

        function showUploadForm(overlay) {
            const panel = actionPanel(overlay);
            if (!(panel instanceof HTMLElement) || !activeUploadFiles.length) return;
            const selectedFiles = activeUploadFiles;
            const selectedDestination = activeUploadDestination;
            const selectedImportDestination = activeImportDestination;
            closeActionPanel(overlay, { restoreFocus: false });
            activeUploadFiles = selectedFiles;
            activeUploadDestination = selectedDestination;
            activeImportDestination = selectedImportDestination;
            const importDestinationMarkup = `
                <input type="hidden" name="import_destination" value="${escapeHtml(activeImportDestination)}" />
                <div class="vault-explorer-move-destination">
                    <span>Markdown destination</span>
                    <strong class="cell-mono" data-vault-explorer-import-destination>${escapeHtml(activeImportDestination || 'Vault root')}</strong>
                    <button type="button" class="ui-button-secondary" data-vault-explorer-upload-change-import-destination>Change</button>
                </div>`;
            panel.innerHTML = `
                <div class="vault-explorer-action-header">
                    <strong>Upload files</strong>
                    <button type="button" class="ui-icon-button is-compact" data-vault-explorer-action-cancel aria-label="Cancel" title="Cancel">${icons.X_ICON_SVG}</button>
                </div>
                <form class="vault-explorer-upload-form" data-vault-explorer-upload-form>
                    <input type="hidden" name="destination" value="${escapeHtml(activeUploadDestination)}" />
                    <div class="vault-explorer-move-destination">
                        <span>Destination</span>
                        <strong class="cell-mono" data-vault-explorer-upload-destination>${escapeHtml(activeUploadDestination || 'Vault root')}</strong>
                        <button type="button" class="ui-button-secondary" data-vault-explorer-upload-change-destination>Change</button>
                    </div>
                    <div class="vault-explorer-upload-list" data-vault-explorer-upload-list></div>
                    <p class="text-xs text-txt-secondary">Upload the source files only, or upload supported PDFs/images and import them to Markdown in one operation.</p>
                    ${window.VaultExplorerImportOptions.renderMarkup(importDestinationMarkup)}
                    <div class="vault-explorer-form-actions">
                        <button type="button" class="ui-button-secondary" data-vault-explorer-action-cancel>Cancel</button>
                        <button type="submit" class="ui-button-primary">Upload</button>
                        <button type="submit" name="upload_mode" value="upload_import" class="ui-button-primary"
                            ${activeUploadFiles.every((file) => window.VaultExplorerImports.supportsPath(file.name)) ? '' : 'disabled title="Every file must be a supported PDF or image."'}>
                            Upload &amp; import
                        </button>
                    </div>
                    <div class="text-sm" data-vault-explorer-form-status></div>
                </form>`;
            panel.classList.remove('hidden');
            overlay.classList.add('vault-explorer-preparing-upload');
            renderUploadFiles(panel);
            const uploadForm = panel.querySelector('[data-vault-explorer-upload-form]');
            if (uploadForm instanceof HTMLFormElement) {
                window.VaultExplorerImportOptions.bind(uploadForm);
            }
            panel.querySelector('[data-vault-explorer-upload-change-destination]')
                ?.addEventListener('click', () => {
                    destinationMode.begin({
                        initialPath: activeUploadDestination,
                        purpose: 'upload',
                    });
                    overlay.classList.add('vault-explorer-choosing-destination');
                    syncDestinationSelection(overlay);
                });
            panel.querySelector('[data-vault-explorer-upload-change-import-destination]')
                ?.addEventListener('click', () => {
                    destinationMode.begin({
                        initialPath: activeImportDestination,
                        purpose: 'upload_import',
                    });
                    overlay.classList.add('vault-explorer-choosing-destination');
                    syncDestinationSelection(overlay);
                });
            panel.querySelector('button[type="submit"]')?.focus();
        }

        function renderUploadFiles(panel) {
            const list = panel.querySelector('[data-vault-explorer-upload-list]');
            if (!(list instanceof HTMLElement)) return;
            list.innerHTML = activeUploadFiles.map((file) => `
                <div class="vault-explorer-upload-item">
                    <span title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
                    <span class="text-txt-secondary">${formatFileSize(file.size)}</span>
                </div>
            `).join('');
        }

        async function submitUploads(
            overlay,
            form,
            options,
            { importAfterUpload = false } = {}
        ) {
            if (isReadOnly(options) || !activeUploadFiles.length) return;
            const destinationInput = form.elements.namedItem('destination');
            const destination = destinationInput instanceof HTMLInputElement
                ? destinationInput.value.trim().replace(/\/+$/, '')
                : '';
            const invalidDestination = destination.startsWith('/')
                || destination.includes('\\')
                || destination.split('/').includes('..');
            if (invalidDestination) {
                const message = 'Enter a vault-relative destination folder.';
                if (destinationInput instanceof HTMLInputElement) {
                    destinationInput.setCustomValidity(message);
                    destinationInput.reportValidity();
                }
                return;
            }

            const submit = form.querySelector('button[type="submit"]');
            const status = form.querySelector('[data-vault-explorer-form-status]');
            const failures = [];
            const uploadedPaths = [];
            uploadInProgress = true;
            setUploadInteractionState(overlay, form, true);
            callbacks.syncInteractionLocks();
            try {
                for (const file of activeUploadFiles) {
                    const path = joinPath(destination, file.name);
                    if (status) status.textContent = `Uploading ${file.name}...`;
                    try {
                        if (typeof options.onUpload !== 'function') {
                            throw new Error('File uploads are unavailable.');
                        }
                        const result = await options.onUpload(file, path);
                        uploadedPaths.push(result?.path || path);
                    } catch (error) {
                        failures.push({ file, message: error.message });
                    }
                }
            } finally {
                uploadInProgress = false;
                setUploadInteractionState(overlay, form, false);
            }

            if (uploadedPaths.length) {
                try {
                    await callbacks.refreshExplorer(overlay, options, uploadedPaths[0]);
                } catch (refreshError) {
                    callbacks.setStatus(
                        overlay,
                        `Upload succeeded, but the Explorer could not refresh: ${refreshError.message}`,
                        true
                    );
                }
            }
            let importError = null;
            if (importAfterUpload && uploadedPaths.length) {
                uploadInProgress = true;
                setUploadInteractionState(overlay, form, true);
                callbacks.syncInteractionLocks();
                try {
                    if (typeof options.onImportSources !== 'function') {
                        throw new Error('Content import is unavailable.');
                    }
                    if (status) status.textContent = 'Upload complete. Importing to Markdown…';
                    await options.onImportSources({
                        ...window.VaultExplorerImportOptions.read(form),
                        sources: uploadedPaths,
                        destination: activeImportDestination,
                    });
                } catch (error) {
                    importError = error;
                } finally {
                    uploadInProgress = false;
                    setUploadInteractionState(overlay, form, false);
                }
            }
            if (!failures.length) {
                if (importError) {
                    activeUploadFiles = [];
                    form.querySelectorAll('button[type="submit"]').forEach((button) => {
                        if (button instanceof HTMLButtonElement) button.disabled = true;
                    });
                    if (status) {
                        status.innerHTML = `<span class="state-error">Upload succeeded, but import failed: ${escapeHtml(importError.message)}</span>`;
                    }
                    return;
                }
                closeActionPanel(overlay, { restoreFocus: false });
                callbacks.setStatus(
                    overlay,
                    importAfterUpload
                        ? `${uploadedPaths.length} file${uploadedPaths.length === 1 ? '' : 's'} uploaded and imported.`
                        : `${uploadedPaths.length} file${uploadedPaths.length === 1 ? '' : 's'} uploaded.`
                );
                return;
            }

            activeUploadFiles = failures.map(({ file }) => file);
            renderUploadFiles(form);
            const importButton = form.querySelector('button[value="upload_import"]');
            if (importButton instanceof HTMLButtonElement) {
                importButton.disabled = !activeUploadFiles.every((file) => (
                    window.VaultExplorerImports.supportsPath(file.name)
                ));
            }
            if (status) {
                status.innerHTML = (importError
                    ? `<div class="state-error">Import failed: ${escapeHtml(importError.message)}</div>`
                    : '') + failures.map(({ file, message }) => (
                    `<div class="state-error">${escapeHtml(file.name)}: ${escapeHtml(message)}</div>`
                )).join('');
            }
            if (submit instanceof HTMLButtonElement) submit.disabled = false;
        }

        function setUploadInteractionState(overlay, form, busy) {
            form.querySelectorAll('button, input').forEach((control) => {
                if (
                    control instanceof HTMLButtonElement
                    || control instanceof HTMLInputElement
                ) {
                    control.disabled = busy || (
                        !busy
                        && control instanceof HTMLButtonElement
                        && control.value === 'upload_import'
                        && !activeUploadFiles.every((file) => (
                            window.VaultExplorerImports.supportsPath(file.name)
                        ))
                    );
                }
            });
            const closeButton = overlay.querySelector('[data-vault-path-picker-close]');
            if (closeButton instanceof HTMLButtonElement) closeButton.disabled = busy;
            if (!busy) callbacks.syncInteractionLocks();
        }

        function formatFileSize(bytes) {
            if (bytes < 1024) return `${bytes} B`;
            if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
            return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        }

        function showCreateForm(overlay, kind, parent) {
            showMutationForm(overlay, {
                operation: kind === 'directory' ? 'create_directory' : 'create_file',
                title: kind === 'directory' ? 'Create folder' : 'Create file',
                label: kind === 'directory' ? 'Folder name' : 'File name',
                value: '',
                submitLabel: 'Create',
                parent,
            });
        }

        function showMutationForm(overlay, { operation, title, label = '', value = '', submitLabel, path = '', kind = '', parent = '' }) {
            const panel = actionPanel(overlay);
            if (!(panel instanceof HTMLElement)) return;
            const isDelete = operation === 'delete';
            panel.innerHTML = `
                <div class="vault-explorer-action-header">
                    <strong>${escapeHtml(title)}</strong>
                    <button type="button" class="ui-icon-button is-compact" data-vault-explorer-action-cancel aria-label="Cancel" title="Cancel">${icons.X_ICON_SVG}</button>
                </div>
                <form class="vault-explorer-mutation-form" data-vault-explorer-mutation-form data-operation="${operation}" data-path="${escapeHtml(path)}" data-kind="${escapeHtml(kind)}" data-parent="${escapeHtml(parent)}">
                    ${isDelete ? `<p>Delete <span class="cell-mono">${escapeHtml(path)}</span>?</p>` : `<label>${escapeHtml(label)}<input name="value" value="${escapeHtml(value)}" class="vault-explorer-path-input" autocomplete="off" required /></label>`}
                    <div class="vault-explorer-form-actions">
                        <button type="button" class="ui-button-secondary" data-vault-explorer-action-cancel>Cancel</button>
                        <button type="submit" class="${isDelete ? 'ui-button-danger' : 'ui-button-primary'}">${escapeHtml(submitLabel)}</button>
                    </div>
                    <div class="text-sm" data-vault-explorer-form-status></div>
                </form>`;
            panel.classList.remove('hidden');
            const input = panel.querySelector('input');
            input?.focus();
            if (operation === 'rename' && input instanceof HTMLInputElement) {
                const extensionIndex = kind === 'file' ? value.lastIndexOf('.') : -1;
                input.setSelectionRange(0, extensionIndex > 0 ? extensionIndex : value.length);
            }
        }

        function showMoveForm(overlay, { path, kind }) {
            const panel = actionPanel(overlay);
            if (!(panel instanceof HTMLElement)) return;
            const initialParent = parentPath(path);
            const name = baseName(path);
            const workspace = workspacePath();
            panel.innerHTML = `
                <div class="vault-explorer-action-header">
                    <strong>Move ${escapeHtml(kind)}</strong>
                    <button type="button" class="ui-icon-button is-compact" data-vault-explorer-action-cancel aria-label="Cancel" title="Cancel">${icons.X_ICON_SVG}</button>
                </div>
                <form class="vault-explorer-mutation-form vault-explorer-move-form" data-vault-explorer-mutation-form data-operation="move" data-path="${escapeHtml(path)}" data-kind="${escapeHtml(kind)}" data-destination="${escapeHtml(initialParent)}">
                    <p class="text-txt-secondary">Choose a destination folder in the tree.</p>
                    <div class="vault-explorer-move-destination">
                        <span>Destination</span>
                        <strong class="cell-mono" data-vault-explorer-move-destination>${escapeHtml(initialParent || 'Vault root')}</strong>
                        ${workspace ? `<button type="button" class="ui-button-secondary" data-vault-explorer-move-shortcut="${escapeHtml(workspace)}">Workspace root</button>` : ''}
                        <button type="button" class="ui-button-secondary" data-vault-explorer-move-shortcut="">Vault root</button>
                    </div>
                    <label>${kind === 'directory' ? 'Folder name' : 'File name'}
                        <input name="value" value="${escapeHtml(name)}" class="vault-explorer-path-input" data-vault-explorer-move-name autocomplete="off" required />
                    </label>
                    <div class="vault-explorer-move-preview">
                        <span class="text-txt-secondary">New path</span>
                        <span class="cell-mono text-txt-primary" data-vault-explorer-move-preview></span>
                    </div>
                    <div class="vault-explorer-form-actions">
                        <button type="button" class="ui-button-secondary" data-vault-explorer-action-cancel>Cancel</button>
                        <button type="submit" class="ui-button-primary">Move</button>
                    </div>
                    <div class="text-sm" data-vault-explorer-form-status></div>
                </form>`;
            panel.classList.remove('hidden');
            overlay.classList.add('vault-explorer-choosing-destination');
            destinationMode.begin({
                initialPath: initialParent,
                purpose: 'move',
                sourceKind: kind,
                sourcePath: path,
            });
            panel.querySelectorAll('[data-vault-explorer-move-shortcut]').forEach((button) => {
                button.addEventListener('click', () => {
                    selectDestination(
                        overlay,
                        button.getAttribute('data-vault-explorer-move-shortcut') || ''
                    );
                });
            });
            updateMovePreview(overlay);
            syncDestinationSelection(overlay);
        }

        async function handleAction(overlay, { action, path = '', kind = '' }, options) {
            if (action === 'reference') return options.onAddReference?.(path);
            if (action === 'workspace') return options.onSetWorkspace?.(path);
            if (action === 'create_file') return showCreateForm(overlay, 'file', path);
            if (action === 'create_directory') return showCreateForm(overlay, 'directory', path);
            if (action === 'rename') {
                showMutationForm(overlay, {
                    operation: 'rename',
                    title: `Rename ${kind}`,
                    label: 'New name',
                    value: baseName(path),
                    submitLabel: 'Rename',
                    path,
                    kind,
                });
            } else if (action === 'move') {
                showMoveForm(overlay, { path, kind });
            } else if (action === 'delete') {
                showMutationForm(overlay, { operation: 'delete', title: `Delete ${kind}`, submitLabel: 'Delete', path, kind });
            }
        }

        async function submitMutation(overlay, form, options) {
            const operation = form.dataset.operation || '';
            const directPath = form.dataset.directPath === 'true';
            const sourcePath = form.dataset.path || '';
            const parent = form.dataset.parent || '';
            const valueInput = form.elements.namedItem('value');
            const value = valueInput instanceof HTMLInputElement ? valueInput.value.trim() : '';
            const status = form.querySelector('[data-vault-explorer-form-status]');
            const submit = form.querySelector('button[type="submit"]');
            if (isReadOnly(options)) {
                if (status) status.innerHTML = '<span class="state-error">Wait for the active response to finish.</span>';
                return;
            }
            if (submit instanceof HTMLButtonElement) submit.disabled = true;
            const invalidCreateValue = operation.startsWith('create_') && (
                !value
                || value === '.'
                || value === '..'
                || (!directPath && /[\\/]/.test(value))
            );
            const invalidLocalName = ['rename', 'move'].includes(operation) && (
                !value
                || value === '.'
                || value === '..'
                || /[\\/]/.test(value)
            );
            if (invalidCreateValue || invalidLocalName) {
                const message = directPath
                    ? 'Enter a vault-relative path.'
                    : 'Enter a name without path separators.';
                if (valueInput instanceof HTMLInputElement) {
                    valueInput.setCustomValidity(message);
                    valueInput.reportValidity();
                }
                if (status) status.innerHTML = `<span class="state-error">${escapeHtml(message)}</span>`;
                if (submit instanceof HTMLButtonElement) submit.disabled = false;
                return;
            }
            if (
                operation === 'rename'
                && joinPath(parentPath(sourcePath), value) === sourcePath
            ) {
                const message = 'Enter a different name.';
                if (valueInput instanceof HTMLInputElement) {
                    valueInput.setCustomValidity(message);
                    valueInput.reportValidity();
                }
                if (status) {
                    status.innerHTML = `<span class="state-error">${escapeHtml(message)}</span>`;
                }
                if (submit instanceof HTMLButtonElement) submit.disabled = false;
                return;
            }
            if (valueInput instanceof HTMLInputElement) valueInput.setCustomValidity('');
            if (status) status.textContent = 'Working...';
            try {
                const destination = operation === 'rename'
                    ? joinPath(parentPath(sourcePath), value)
                    : operation === 'move'
                        ? joinPath(form.dataset.destination || '', value)
                        : '';
                const targetPath = operation.startsWith('create_')
                    ? (directPath ? value : [parent, value].filter(Boolean).join('/'))
                    : sourcePath;
                const payload = {
                    operation: operation === 'rename' ? 'move' : operation,
                    path: targetPath,
                };
                if (['rename', 'move'].includes(operation)) payload.destination = destination;
                const result = await options.onMutate?.(payload);
                callbacks.mutationCompleted?.({
                    operation,
                    sourcePath,
                    targetPath: result?.path || destination || targetPath,
                    kind: form.dataset.kind || (operation === 'create_directory' ? 'directory' : 'file'),
                });
                closeActionPanel(overlay, { restoreFocus: false });
                const reveal = ['rename', 'move'].includes(operation)
                    ? destination
                    : (operation.startsWith('create_') ? targetPath : parentPath(sourcePath));
                try {
                    await callbacks.refreshExplorer(overlay, options, reveal);
                } catch (refreshError) {
                    callbacks.setStatus(
                        overlay,
                        `The change succeeded, but the Explorer could not refresh: ${refreshError.message}`,
                        true
                    );
                }
                if (operation === 'create_file') options.onOpenFile?.(result?.path || targetPath);
            } catch (error) {
                if (directPath) callbacks.setStatus(overlay, error.message, true);
                if (status) status.innerHTML = `<span class="state-error">${escapeHtml(error.message)}</span>`;
                if (submit instanceof HTMLButtonElement) submit.disabled = false;
            }
        }

        function parentPath(path) {
            const parts = String(path || '').split('/').filter(Boolean);
            parts.pop();
            return parts.join('/');
        }

        function baseName(path) {
            return String(path || '').split('/').filter(Boolean).pop() || '';
        }

        function joinPath(parent, name) {
            return [parent, name].filter(Boolean).join('/');
        }

        function moveForm(overlay) {
            const form = overlay.querySelector(
                '[data-vault-explorer-mutation-form][data-operation="move"], '
                + '[data-vault-explorer-mutation-form][data-operation="batch_move"]'
            );
            return form instanceof HTMLFormElement ? form : null;
        }

        function hasDestinationMode() {
            return destinationMode.snapshot().active;
        }

        function beginDestinationMode(overlay, config) {
            const snapshot = destinationMode.begin(config);
            overlay.classList.add('vault-explorer-choosing-destination');
            syncDestinationSelection(overlay);
            return snapshot;
        }

        function destinationSnapshot() {
            return destinationMode.snapshot();
        }

        function selectDestination(overlay, path) {
            const form = moveForm(overlay);
            const status = form?.querySelector('[data-vault-explorer-form-status]');
            try {
                const destination = destinationMode.select(path);
                if (form) form.dataset.destination = destination.path;
                if (destination.purpose === 'upload') {
                    const previousUploadDestination = activeUploadDestination;
                    activeUploadDestination = destination.path;
                    if (activeImportDestination === previousUploadDestination) {
                        activeImportDestination = destination.path;
                        const importInput = overlay.querySelector(
                            '[data-vault-explorer-upload-form] input[name="import_destination"]'
                        );
                        if (importInput instanceof HTMLInputElement) {
                            importInput.value = destination.path;
                        }
                        const importLabel = overlay.querySelector(
                            '[data-vault-explorer-import-destination]'
                        );
                        if (importLabel) {
                            importLabel.textContent = destination.path || 'Vault root';
                        }
                    }
                    const uploadForm = overlay.querySelector('[data-vault-explorer-upload-form]');
                    const input = uploadForm?.elements.namedItem('destination');
                    if (input instanceof HTMLInputElement) input.value = destination.path;
                    const label = overlay.querySelector('[data-vault-explorer-upload-destination]');
                    if (label) label.textContent = destination.path || 'Vault root';
                    if (uploadForm instanceof HTMLFormElement) renderUploadFiles(uploadForm);
                }
                if (destination.purpose === 'import') {
                    callbacks.importDestinationSelected?.(overlay, destination.path);
                }
                if (destination.purpose === 'upload_import') {
                    activeImportDestination = destination.path;
                    const importInput = overlay.querySelector(
                        '[data-vault-explorer-upload-form] input[name="import_destination"]'
                    );
                    if (importInput instanceof HTMLInputElement) {
                        importInput.value = destination.path;
                    }
                    const importLabel = overlay.querySelector(
                        '[data-vault-explorer-import-destination]'
                    );
                    if (importLabel) importLabel.textContent = destination.path || 'Vault root';
                }
                if (status) status.textContent = '';
                const destinationLabel = form?.querySelector(
                    '[data-vault-explorer-move-destination]'
                );
                if (destinationLabel) {
                    destinationLabel.textContent = destination.path || 'Vault root';
                }
                updateMovePreview(overlay);
                syncDestinationSelection(overlay);
            } catch (error) {
                if (status) {
                    status.innerHTML = `<span class="state-error">${escapeHtml(error.message)}</span>`;
                }
            }
        }

        function updateMovePreview(overlay) {
            const form = moveForm(overlay);
            if (!form) return;
            if (form.dataset.operation === 'batch_move') return;
            const destination = destinationMode.snapshot().path;
            const nameInput = form.querySelector('[data-vault-explorer-move-name]');
            const name = nameInput instanceof HTMLInputElement ? nameInput.value.trim() : '';
            const destinationLabel = form.querySelector('[data-vault-explorer-move-destination]');
            const preview = form.querySelector('[data-vault-explorer-move-preview]');
            const submit = form.querySelector('button[type="submit"]');
            const newPath = joinPath(destination, name);
            if (destinationLabel) destinationLabel.textContent = destination || 'Vault root';
            if (preview) preview.textContent = newPath || 'Choose a name';
            if (submit instanceof HTMLButtonElement) {
                submit.disabled = !name || newPath === (form.dataset.path || '');
            }
        }

        function syncDestinationSelection(overlay) {
            const destination = destinationMode.snapshot();
            overlay.querySelectorAll('[data-vault-path-picker-row]').forEach((row) => {
                if (!(row instanceof HTMLElement)) return;
                const rowPath = row.getAttribute('data-vault-path-picker-row') || '';
                const rowButton = row.querySelector(
                    ':scope > .workspace-tree-row [data-vault-path-picker-select]'
                );
                const selected = destination.active && rowPath === destination.path;
                rowButton?.classList.toggle('is-move-destination', selected);
                rowButton?.setAttribute('aria-selected', selected ? 'true' : 'false');
            });
        }

        return Object.freeze({
            beginDestinationMode,
            closeActionPanel,
            destinationSnapshot,
            handleAction,
            hasDestinationMode,
            isBusy,
            reset,
            selectDestination,
            setUploadFiles,
            submitMutation,
            submitUploads,
            syncDestinationSelection,
            updateMovePreview,
        });
    }

    window.VaultExplorerActions = Object.freeze({
        create: createVaultExplorerActionsController,
    });
})(window);
