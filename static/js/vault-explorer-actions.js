(function vaultExplorerActionsModule(window) {
    function createVaultExplorerActionsController({ icons, utils, callbacks }) {
        const { escapeHtml } = utils;
        let activeUploadFiles = [];
        let uploadInProgress = false;

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
            uploadInProgress = false;
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
            const sourcePath = panel.querySelector(
                '[data-vault-explorer-mutation-form]'
            )?.getAttribute('data-path') || '';
            panel.classList.add('hidden');
            panel.innerHTML = '';
            overlay.classList.remove('vault-explorer-choosing-destination');
            overlay.classList.remove('vault-explorer-preparing-upload');
            syncMoveDestinationSelection(overlay);
            if (wasUpload) activeUploadFiles = [];
            if (restoreFocus && sourcePath) {
                const sourceMenuButton = Array.from(
                    overlay.querySelectorAll('[data-vault-explorer-more]')
                ).find((button) => (
                    button.getAttribute('data-vault-explorer-more') === sourcePath
                ));
                sourceMenuButton?.focus();
            } else if (restoreFocus && wasUpload) {
                overlay.querySelector('[data-vault-explorer-upload]')?.focus();
            }
        }

        function setUploadFiles(overlay, files) {
            activeUploadFiles = Array.from(files || []);
            if (activeUploadFiles.length) showUploadForm(overlay);
        }

        function showUploadForm(overlay) {
            const panel = actionPanel(overlay);
            if (!(panel instanceof HTMLElement) || !activeUploadFiles.length) return;
            const selectedFiles = activeUploadFiles;
            closeActionPanel(overlay, { restoreFocus: false });
            activeUploadFiles = selectedFiles;
            closeHeaderCreateForm(overlay);
            const scope = overlay.querySelector('[data-vault-path-picker-scope]');
            const initialDestination = scope instanceof HTMLSelectElement
                && scope.value === 'workspace'
                ? workspacePath()
                : '';
            panel.innerHTML = `
                <div class="vault-explorer-action-header">
                    <strong>Upload files</strong>
                    <button type="button" class="ui-icon-button is-compact" data-vault-explorer-action-cancel aria-label="Cancel" title="Cancel">${icons.X_ICON_SVG}</button>
                </div>
                <form class="vault-explorer-upload-form" data-vault-explorer-upload-form>
                    <label>Destination folder
                        <input name="destination" value="${escapeHtml(initialDestination)}" class="vault-explorer-path-input" autocomplete="off" placeholder="Vault root" />
                    </label>
                    <div class="vault-explorer-upload-list" data-vault-explorer-upload-list></div>
                    <p class="text-xs text-txt-secondary">To convert PDFs or images to Markdown, upload them to <span class="cell-mono">AssistantMD/Import</span>, then use Import Files.</p>
                    <div class="vault-explorer-form-actions">
                        <button type="button" class="ui-button-secondary" data-vault-explorer-action-cancel>Cancel</button>
                        <button type="submit" class="ui-button-primary">Upload</button>
                    </div>
                    <div class="text-sm" data-vault-explorer-form-status></div>
                </form>`;
            panel.classList.remove('hidden');
            overlay.classList.add('vault-explorer-preparing-upload');
            const destinationInput = panel.querySelector('input[name="destination"]');
            destinationInput?.addEventListener('input', () => {
                destinationInput.setCustomValidity('');
                renderUploadPaths(panel);
            });
            renderUploadPaths(panel);
            destinationInput?.focus();
            if (destinationInput instanceof HTMLInputElement) {
                destinationInput.setSelectionRange(
                    destinationInput.value.length,
                    destinationInput.value.length
                );
            }
        }

        function renderUploadPaths(panel) {
            const list = panel.querySelector('[data-vault-explorer-upload-list]');
            const destinationInput = panel.querySelector('input[name="destination"]');
            if (!(list instanceof HTMLElement)) return;
            const destination = destinationInput instanceof HTMLInputElement
                ? destinationInput.value.trim().replace(/\/+$/, '')
                : '';
            list.innerHTML = activeUploadFiles.map((file) => `
                <div class="vault-explorer-upload-item">
                    <span title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
                    <span class="cell-mono text-txt-secondary">${escapeHtml(joinPath(destination, file.name))}</span>
                    <span class="text-txt-secondary">${formatFileSize(file.size)}</span>
                </div>
            `).join('');
        }

        async function submitUploads(overlay, form, options) {
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
            setUploadInteractionState(overlay, form, true);
            const failures = [];
            const uploadedPaths = [];
            uploadInProgress = true;
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
            if (!failures.length) {
                closeActionPanel(overlay, { restoreFocus: false });
                return;
            }

            activeUploadFiles = failures.map(({ file }) => file);
            renderUploadPaths(form);
            if (status) {
                status.innerHTML = failures.map(({ file, message }) => (
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
                    control.disabled = busy;
                }
            });
            const closeButton = overlay.querySelector('[data-vault-path-picker-close]');
            if (closeButton instanceof HTMLButtonElement) closeButton.disabled = busy;
            overlay.querySelectorAll(
                '[data-vault-explorer-upload], [data-vault-explorer-refresh]'
            ).forEach((control) => {
                if (control instanceof HTMLButtonElement) control.disabled = busy;
            });
            if (!busy) callbacks.syncInteractionLocks();
        }

        function formatFileSize(bytes) {
            if (bytes < 1024) return `${bytes} B`;
            if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
            return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        }

        function toggleHeaderCreateMenu(overlay) {
            const menu = overlay.querySelector('[data-vault-explorer-new-menu]');
            if (!(menu instanceof HTMLElement)) return;
            closeHeaderCreateForm(overlay);
            menu.classList.toggle('hidden');
        }

        function closeHeaderCreateForm(overlay) {
            const form = overlay.querySelector('[data-vault-explorer-header-create]');
            const menu = overlay.querySelector('[data-vault-explorer-new-menu]');
            form?.classList.add('hidden');
            menu?.classList.add('hidden');
            if (form instanceof HTMLFormElement) {
                form.dataset.operation = '';
                form.querySelector('input')?.setCustomValidity('');
            }
        }

        function showHeaderCreateForm(overlay, kind) {
            const form = overlay.querySelector('[data-vault-explorer-header-create]');
            const input = form?.querySelector('input');
            const label = form?.querySelector('[data-vault-explorer-header-create-label]');
            if (!(form instanceof HTMLFormElement) || !(input instanceof HTMLInputElement)) return;

            const scope = overlay.querySelector('[data-vault-path-picker-scope]');
            const parent = scope instanceof HTMLSelectElement
                && scope.value === 'workspace'
                ? workspacePath()
                : '';
            const normalizedKind = kind === 'directory' ? 'directory' : 'file';
            const labelText = normalizedKind === 'directory' ? 'New folder path' : 'New file path';
            form.dataset.operation = normalizedKind === 'directory'
                ? 'create_directory'
                : 'create_file';
            input.value = parent ? `${parent}/` : '';
            input.placeholder = normalizedKind === 'directory'
                ? 'Folder/path'
                : 'Folder/file.md';
            input.setAttribute('aria-label', labelText);
            if (label instanceof HTMLElement) label.textContent = labelText;
            form.classList.remove('hidden');
            overlay.querySelector('[data-vault-explorer-new-menu]')?.classList.add('hidden');
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
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
            panel.querySelectorAll('[data-vault-explorer-move-shortcut]').forEach((button) => {
                button.addEventListener('click', () => {
                    selectMoveDestination(
                        overlay,
                        button.getAttribute('data-vault-explorer-move-shortcut') || ''
                    );
                });
            });
            updateMovePreview(overlay);
            syncMoveDestinationSelection(overlay);
        }

        function toggleRowMenu(overlay, button) {
            const menu = button.parentElement?.querySelector('[data-vault-explorer-row-menu]');
            overlay.querySelectorAll('[data-vault-explorer-row-menu]').forEach((candidate) => {
                if (candidate !== menu) candidate.classList.add('hidden');
            });
            menu?.classList.toggle('hidden');
        }

        async function handleRowAction(overlay, button, options) {
            const action = button.dataset.vaultExplorerRowAction || '';
            const path = button.dataset.path || '';
            const kind = button.dataset.kind || '';
            button.closest('[data-vault-explorer-row-menu]')?.classList.add('hidden');
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
                closeActionPanel(overlay, { restoreFocus: false });
                closeHeaderCreateForm(overlay);
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
                '[data-vault-explorer-mutation-form][data-operation="move"]'
            );
            return form instanceof HTMLFormElement ? form : null;
        }

        function hasMoveForm(overlay) {
            return Boolean(moveForm(overlay));
        }

        function selectMoveDestination(overlay, destination) {
            const form = moveForm(overlay);
            if (!form) return;
            const source = form.dataset.path || '';
            const kind = form.dataset.kind || '';
            const status = form.querySelector('[data-vault-explorer-form-status]');
            if (
                kind === 'directory'
                && (destination === source || destination.startsWith(`${source}/`))
            ) {
                if (status) {
                    status.innerHTML = '<span class="state-error">A folder cannot be moved into itself.</span>';
                }
                return;
            }
            form.dataset.destination = destination;
            if (status) status.textContent = '';
            updateMovePreview(overlay);
            syncMoveDestinationSelection(overlay);
        }

        function updateMovePreview(overlay) {
            const form = moveForm(overlay);
            if (!form) return;
            const destination = form.dataset.destination || '';
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

        function syncMoveDestinationSelection(overlay) {
            const form = moveForm(overlay);
            const destination = form?.dataset.destination;
            overlay.querySelectorAll('[data-vault-path-picker-row]').forEach((row) => {
                if (!(row instanceof HTMLElement)) return;
                const rowPath = row.getAttribute('data-vault-path-picker-row') || '';
                const rowButton = row.querySelector(
                    ':scope > .workspace-tree-row [data-vault-path-picker-select]'
                );
                const selected = destination !== undefined && rowPath === destination;
                rowButton?.classList.toggle('is-move-destination', selected);
                rowButton?.setAttribute('aria-selected', selected ? 'true' : 'false');
            });
        }

        return Object.freeze({
            closeActionPanel,
            closeHeaderCreateForm,
            handleRowAction,
            hasMoveForm,
            isBusy,
            reset,
            selectMoveDestination,
            setUploadFiles,
            showHeaderCreateForm,
            submitMutation,
            submitUploads,
            syncMoveDestinationSelection,
            toggleHeaderCreateMenu,
            toggleRowMenu,
            updateMovePreview,
        });
    }

    window.VaultExplorerActions = Object.freeze({
        create: createVaultExplorerActionsController,
    });
})(window);
