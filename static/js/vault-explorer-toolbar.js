(function vaultExplorerToolbarModule(window) {
    const ACTIONS = Object.freeze({
        new_file: { label: 'New file', locks: true },
        new_directory: { label: 'New folder', locks: true },
        upload: { label: 'Upload', locks: true },
        import_url: { label: 'Import URL', locks: true },
        refresh: { label: 'Refresh', locks: false },
        open: { label: 'Open', locks: false },
        reference: { label: 'Add to prompt', locks: true },
        copy: { label: 'Copy path', locks: false },
        import_file: { label: 'Import to Markdown', locks: true },
        workspace: { label: 'Set as workspace', locks: true },
        rename: { label: 'Rename', locks: true },
        move: { label: 'Move', locks: true },
        delete: { label: 'Delete', locks: true, danger: true },
    });

    function createVaultExplorerToolbarController({ utils, callbacks = {} }) {
        const { escapeHtml } = utils;
        let container = null;
        let currentSnapshot = null;

        function mount(nextContainer) {
            if (container === nextContainer) return;
            destroy();
            container = nextContainer;
            container?.addEventListener?.('click', handleClick);
        }

        function render(snapshot, {
            lockMessage = 'Available when the active response finishes.',
            readOnly = false,
            supportedActions = Object.keys(ACTIONS),
        } = {}) {
            currentSnapshot = snapshot;
            if (!container) return;
            const selectedCount = Number(snapshot?.selectedCount || 0);
            const activeFolder = snapshot?.activeFolder || '';
            const actions = Array.from(supportedActions)
                .filter((name) => shouldRenderAction(
                    name,
                    snapshot?.operations?.[name],
                    selectedCount
                ))
                .map((name) => renderActionButton(
                    name,
                    snapshot.operations[name],
                    { lockMessage, readOnly }
                ))
                .join('');
            const clearButton = selectedCount
                ? renderClearButton(snapshot.operations?.clear)
                : '';
            container.innerHTML = `
                <div class="vault-explorer-toolbar-summary">
                    <nav class="vault-explorer-toolbar-location" aria-label="Active folder">
                        <span class="vault-explorer-toolbar-label">Folder</span>
                        ${renderLocation(activeFolder)}
                    </nav>
                    <span class="vault-explorer-toolbar-selection">${selectedCount ? `${selectedCount} selected` : 'No selection'}</span>
                </div>
                <div class="vault-explorer-toolbar-actions">
                    ${actions}
                    ${clearButton}
                </div>
            `;
        }

        function renderLocation(activeFolder) {
            const segments = String(activeFolder || '').split('/').filter(Boolean);
            const crumbs = [{ label: 'Vault root', path: '' }];
            let path = '';
            for (const segment of segments) {
                path = path ? `${path}/${segment}` : segment;
                crumbs.push({ label: segment, path });
            }
            return crumbs.map((crumb, index) => `
                ${index ? '<span class="vault-explorer-toolbar-separator" aria-hidden="true">/</span>' : ''}
                <button type="button" class="vault-explorer-toolbar-crumb cell-mono"
                    data-vault-explorer-location="${escapeHtml(crumb.path)}"
                    ${index === crumbs.length - 1 ? 'aria-current="location"' : ''}>
                    ${escapeHtml(crumb.label)}
                </button>
            `).join('');
        }

        function shouldRenderAction(name, operationState, selectedCount) {
            if (!ACTIONS[name] || !operationState) return false;
            if (operationState.enabled) return true;
            return selectedCount > 1 && ['move', 'delete'].includes(name);
        }

        function renderActionButton(name, operationState, { lockMessage, readOnly }) {
            const definition = ACTIONS[name];
            const locked = readOnly && definition.locks;
            const disabled = locked || operationState.enabled !== true;
            const reason = locked
                ? lockMessage
                : operationState.reason || definition.label;
            const classes = [
                'ui-button-secondary',
                'vault-explorer-toolbar-action',
                definition.danger ? 'is-danger' : '',
            ].filter(Boolean).join(' ');
            return `
                <button type="button" class="${classes}"
                    data-vault-explorer-toolbar-action="${name}"
                    aria-label="${escapeHtml(definition.label)}"
                    title="${escapeHtml(reason)}" ${disabled ? 'disabled' : ''}>
                    ${escapeHtml(definition.label)}
                </button>
            `;
        }

        function renderClearButton(operationState) {
            const disabled = operationState?.enabled !== true;
            const reason = operationState?.reason || 'Clear selection';
            return `
                <button type="button" class="vault-explorer-toolbar-clear"
                    data-vault-explorer-toolbar-action="clear"
                    aria-label="Clear selection" title="${escapeHtml(reason)}"
                    ${disabled ? 'disabled' : ''}>Clear selection</button>
            `;
        }

        function handleClick(event) {
            const button = event.target?.closest?.('[data-vault-explorer-toolbar-action]');
            if (button) {
                if (button.disabled) return;
                const action = button.getAttribute('data-vault-explorer-toolbar-action') || '';
                if (!action || !currentSnapshot) return;
                callbacks.onAction?.(action, currentSnapshot, button);
                return;
            }
            const location = event.target?.closest?.('[data-vault-explorer-location]');
            if (!location || !currentSnapshot) return;
            callbacks.onLocation?.(
                location.getAttribute('data-vault-explorer-location') || '',
                currentSnapshot
            );
        }

        function destroy() {
            container?.removeEventListener?.('click', handleClick);
            if (container) container.innerHTML = '';
            container = null;
            currentSnapshot = null;
        }

        return Object.freeze({ destroy, mount, render });
    }

    window.VaultExplorerToolbar = Object.freeze({
        create: createVaultExplorerToolbarController,
    });
})(window);
