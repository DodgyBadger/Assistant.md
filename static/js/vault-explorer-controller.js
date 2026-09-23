(function vaultExplorerControllerModule(window) {
    function createVaultExplorerController({ utils, callbacks }) {
        const { flashCopyFeedback, handleCopy } = utils;
        let overlay = null;
        let options = null;
        let toolbar = null;
        const state = window.VaultExplorerState.create({ onChange: render });
        toolbar = window.VaultExplorerToolbar.create({
            utils,
            callbacks: { onAction: handleToolbarAction },
        });

        function open(nextOverlay, nextOptions, { activeFolder = '' } = {}) {
            overlay = nextOverlay;
            options = nextOptions;
            toolbar.mount(overlay.querySelector('[data-vault-explorer-toolbar]'));
            state.reset({ activeFolder });
        }

        function close() {
            toolbar.destroy();
            overlay = null;
            options = null;
            state.reset();
        }

        function snapshot() {
            return state.snapshot();
        }

        function setActiveFolder(path) {
            state.setActiveFolder(path);
        }

        function toggleSelection(item) {
            state.toggle(item);
        }

        function mutationCompleted({ operation, sourcePath, targetPath, kind }) {
            if (operation === 'delete') {
                state.deselect(sourcePath);
                return;
            }
            if (['rename', 'move'].includes(operation) && targetPath) {
                state.deselect(sourcePath);
                state.select({ path: targetPath, kind, importEligible: false });
            }
        }

        async function handleToolbarAction(action, currentSnapshot, button) {
            if (!overlay || !options) return;
            const selectedItem = currentSnapshot.selectedItems[0] || null;
            try {
                if (action === 'clear') {
                    state.clearSelection();
                    return;
                }
                if (action === 'refresh') {
                    await callbacks.refreshExplorer(overlay, options);
                    return;
                }
                if (action === 'upload') {
                    overlay.querySelector('[data-vault-explorer-upload-input]')?.click();
                    return;
                }
                if (action === 'new_file' || action === 'new_directory') {
                    await callbacks.handleMutationAction(overlay, {
                        action: action === 'new_file' ? 'create_file' : 'create_directory',
                        path: currentSnapshot.destinationPath,
                        kind: 'directory',
                    }, options);
                    return;
                }
                if (action === 'open' && selectedItem) {
                    if (selectedItem.kind === 'directory') {
                        state.setActiveFolder(selectedItem.path);
                        await callbacks.expandDirectory(overlay, selectedItem.path, options);
                    } else {
                        options.onSelect?.(selectedItem);
                    }
                    return;
                }
                if (action === 'reference') {
                    for (const item of currentSnapshot.selectedItems) {
                        options.onAddReference?.(item.path);
                    }
                    return;
                }
                if (action === 'copy' && selectedItem) {
                    flashCopyFeedback(button, await handleCopy(selectedItem.path));
                    return;
                }
                if (action === 'workspace' && selectedItem) {
                    await options.onSetWorkspace?.(selectedItem.path);
                    return;
                }
                if (['rename', 'move', 'delete'].includes(action) && selectedItem) {
                    await callbacks.handleMutationAction(overlay, {
                        action,
                        path: selectedItem.path,
                        kind: selectedItem.kind,
                    }, options);
                }
            } catch (error) {
                callbacks.setStatus(overlay, error.message, true);
            }
        }

        function render(currentSnapshot = state.snapshot()) {
            if (!overlay || !options?.explorer) return;
            const selectedPaths = new Set(currentSnapshot.selectedPaths);
            overlay.querySelectorAll('[data-vault-path-picker-row]').forEach((row) => {
                if (!(row instanceof HTMLElement)) return;
                const path = row.getAttribute('data-vault-path-picker-row') || '';
                const rowContent = row.querySelector(':scope > .workspace-tree-row');
                const selected = selectedPaths.has(path);
                const active = Boolean(row.querySelector(
                    ':scope > .workspace-tree-row [data-vault-path-picker-kind="directory"]'
                )) && path === currentSnapshot.activeFolder;
                rowContent?.classList.toggle('is-selected', selected);
                rowContent?.classList.toggle('is-active-folder', active);
                const checkbox = row.querySelector(
                    ':scope > .workspace-tree-row [data-vault-explorer-select-item]'
                );
                if (checkbox instanceof HTMLInputElement) checkbox.checked = selected;
            });
            toolbar.render(currentSnapshot, {
                readOnly: callbacks.isReadOnly(options) || callbacks.isBusy(),
                lockMessage: callbacks.isBusy()
                    ? 'Available after the upload finishes.'
                    : 'Available when the active response finishes.',
                supportedActions: supportedToolbarActions(options),
            });
        }

        function supportedToolbarActions(currentOptions) {
            const actions = [];
            if (typeof currentOptions.onMutate === 'function') {
                actions.push('new_file', 'new_directory');
            }
            if (typeof currentOptions.onUpload === 'function') actions.push('upload');
            actions.push('refresh', 'open');
            if (typeof currentOptions.onAddReference === 'function') actions.push('reference');
            actions.push('copy');
            if (typeof currentOptions.onSetWorkspace === 'function') actions.push('workspace');
            actions.push('rename', 'move', 'delete');
            return actions;
        }

        return Object.freeze({
            close,
            mutationCompleted,
            open,
            render,
            setActiveFolder,
            snapshot,
            toggleSelection,
        });
    }

    window.VaultExplorerController = Object.freeze({
        create: createVaultExplorerController,
    });
})(window);
