(function vaultExplorerToolbarModule(window) {
    const ACTIONS = Object.freeze({
        new_file: { label: 'New file', icon: 'PLUS_ICON_SVG', locks: true },
        new_directory: { label: 'New folder', icon: 'FOLDER_ICON_SVG', locks: true },
        upload: { label: 'Upload', icon: 'UPLOAD_ICON_SVG', locks: true },
        import_url: { label: 'Import URL', icon: 'LINK_ICON_SVG', locks: true },
        refresh: { label: 'Refresh', icon: 'REFRESH_ICON_SVG', locks: false },
        open: { label: 'Open', icon: 'EYE_ICON_SVG', locks: false },
        reference: { label: 'Add to prompt', icon: 'MESSAGE_SQUARE_PLUS_ICON_SVG', locks: true },
        copy: { label: 'Copy path', icon: 'CLIPBOARD_COPY_ICON_SVG', locks: false },
        import_file: { label: 'Import to Markdown', icon: 'FILE_DOWN_ICON_SVG', locks: true },
        workspace: { label: 'Set as workspace', icon: 'BRIEFCASE_BUSINESS_ICON_SVG', locks: true },
        rename: { label: 'Rename', icon: 'EDIT_ICON_SVG', locks: true },
        move: { label: 'Move', icon: 'MOVE_ICON_SVG', locks: true },
        delete: { label: 'Delete', icon: 'TRASH_ICON_SVG', locks: true, danger: true },
    });
    const ACTION_MENUS = Object.freeze([
        Object.freeze({
            name: 'create',
            label: 'Create or upload',
            icon: 'PLUS_ICON_SVG',
            actions: Object.freeze(['new_file', 'new_directory', 'upload']),
            includeDisabled: false,
        }),
        Object.freeze({
            name: 'import',
            label: 'Import',
            icon: 'IMPORT_ICON_SVG',
            actions: Object.freeze(['import_url', 'import_file']),
            includeDisabled: true,
        }),
    ]);
    const GROUPED_ACTIONS = new Set(ACTION_MENUS.flatMap((menu) => menu.actions));

    function createVaultExplorerToolbarController({ icons = {}, utils, callbacks = {} }) {
        const { escapeHtml } = utils;
        let container = null;
        let locationContainer = null;
        let selectionContainer = null;
        let currentSnapshot = null;

        function mount(nextContainer, nextLocationContainer = null, nextSelectionContainer = null) {
            if (
                container === nextContainer
                && locationContainer === nextLocationContainer
                && selectionContainer === nextSelectionContainer
            ) return;
            destroy();
            container = nextContainer;
            locationContainer = nextLocationContainer;
            selectionContainer = nextSelectionContainer;
            container?.addEventListener?.('click', handleClick);
            container?.addEventListener?.('keydown', handleKeydown);
            locationContainer?.addEventListener?.('click', handleClick);
            selectionContainer?.addEventListener?.('click', handleClick);
            selectionContainer?.addEventListener?.('keydown', handleKeydown);
            window.document?.addEventListener?.('click', handleDocumentClick);
        }

        function render(snapshot, {
            lockMessage = 'Available when the active response finishes.',
            readOnly = false,
            supportedActions = Object.keys(ACTIONS),
            vaultName = '',
            workspacePath = '',
        } = {}) {
            currentSnapshot = snapshot;
            if (!container) return;
            const selectedCount = Number(snapshot?.selectedCount || 0);
            const activeFolder = snapshot?.activeFolder || '';
            const actionNames = Array.from(supportedActions);
            const actionMenus = ACTION_MENUS.map((menu) => renderActionMenu(
                menu,
                actionNames,
                snapshot,
                { lockMessage, readOnly, selectedCount }
            )).join('');
            const actions = actionNames
                .filter((name) => !GROUPED_ACTIONS.has(name))
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
            const selectionMenu = selectedCount ? renderSelectionMenu(selectedCount) : '';
            if (locationContainer) {
                locationContainer.innerHTML = renderHeaderLocation(
                    activeFolder,
                    workspacePath,
                    vaultName
                );
            }
            if (selectionContainer) selectionContainer.innerHTML = selectionMenu;
            container.innerHTML = `
                <div class="vault-explorer-toolbar-actions">
                    ${selectionContainer ? '' : selectionMenu}
                    ${actionMenus}
                    ${actions}
                </div>
            `;
        }

        function renderSelectionMenu(selectedCount) {
            return `
                <div class="vault-explorer-action-menu vault-explorer-selection-menu">
                    <button type="button" class="vault-explorer-toolbar-selection"
                        data-vault-explorer-action-menu-toggle="selection"
                        aria-haspopup="menu" aria-expanded="false"
                        aria-label="${selectedCount} selected" title="Selected items">${selectedCount} selected</button>
                    <div class="vault-explorer-action-menu-options"
                        data-vault-explorer-action-menu-options="selection" role="menu" hidden>
                        <button type="button" class="vault-explorer-action-menu-option"
                            data-vault-explorer-toolbar-action="show_selection" role="menuitem">Show selected</button>
                        <button type="button" class="vault-explorer-action-menu-option"
                            data-vault-explorer-toolbar-action="clear" role="menuitem">Deselect all</button>
                    </div>
                </div>
            `;
        }

        function renderHeaderLocation(activeFolder, workspacePath, vaultName) {
            return `
                <span class="vault-explorer-namespace">${escapeHtml(vaultName || 'Vault')}:</span>
                <nav class="vault-explorer-toolbar-location" aria-label="Active folder">
                    ${renderLocation(activeFolder)}
                </nav>
                <div class="vault-explorer-location-shortcuts" aria-label="Location shortcuts">
                    <button type="button" class="ui-icon-button is-compact"
                        data-vault-explorer-location="" aria-label="Go to vault root" title="Vault root">
                        ${icons.SLASH_ICON_SVG || ''}
                    </button>
                    ${workspacePath ? `
                        <button type="button" class="ui-icon-button is-compact"
                            data-vault-explorer-location="${escapeHtml(workspacePath)}"
                            aria-label="Go to workspace" title="Workspace">
                            ${icons.BRIEFCASE_BUSINESS_ICON_SVG || ''}
                        </button>
                    ` : ''}
                </div>
            `;
        }

        function renderActionMenu(menu, supportedActions, snapshot, {
            lockMessage,
            readOnly,
            selectedCount,
        }) {
            const actionNames = menu.actions.filter((name) => {
                if (!supportedActions.includes(name) || !snapshot?.operations?.[name]) {
                    return false;
                }
                return menu.includeDisabled || shouldRenderAction(
                    name,
                    snapshot.operations[name],
                    selectedCount
                );
            });
            if (!actionNames.length) return '';
            const allDisabled = actionNames.every((name) => (
                (readOnly && ACTIONS[name].locks)
                || snapshot.operations[name].enabled !== true
            ));
            return `
                <div class="vault-explorer-action-menu">
                    <button type="button" class="ui-icon-button is-compact vault-explorer-toolbar-action"
                        data-vault-explorer-action-menu-toggle="${menu.name}" aria-haspopup="menu" aria-expanded="false"
                        aria-label="${escapeHtml(menu.label)}" title="${escapeHtml(readOnly && allDisabled ? lockMessage : menu.label)}"
                        ${allDisabled ? 'disabled' : ''}>${icons[menu.icon] || ''}</button>
                    <div class="vault-explorer-action-menu-options"
                        data-vault-explorer-action-menu-options="${menu.name}" role="menu" hidden>
                        ${actionNames.map((name) => renderMenuOption(
                            name,
                            snapshot.operations[name],
                            { lockMessage, readOnly }
                        )).join('')}
                    </div>
                </div>
            `;
        }

        function renderMenuOption(name, operationState, { lockMessage, readOnly }) {
            const definition = ACTIONS[name];
            const locked = readOnly && definition.locks;
            const disabled = locked || operationState.enabled !== true;
            const reason = locked ? lockMessage : operationState.reason || definition.label;
            return `
                <button type="button" class="vault-explorer-action-menu-option"
                    data-vault-explorer-toolbar-action="${name}" role="menuitem"
                    title="${escapeHtml(reason)}" ${disabled ? 'disabled' : ''}>
                    <span class="vault-explorer-action-menu-option-icon">${icons[definition.icon] || ''}</span>
                    <span>${escapeHtml(definition.label)}</span>
                </button>
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
                'ui-icon-button',
                'is-compact',
                'vault-explorer-toolbar-action',
                definition.danger ? 'is-danger' : '',
            ].filter(Boolean).join(' ');
            return `
                <button type="button" class="${classes}"
                    data-vault-explorer-toolbar-action="${name}"
                    aria-label="${escapeHtml(definition.label)}"
                    title="${escapeHtml(reason)}" ${disabled ? 'disabled' : ''}>
                    ${icons[definition.icon] || ''}
                </button>
            `;
        }

        function handleClick(event) {
            const button = event.target?.closest?.('[data-vault-explorer-toolbar-action]');
            if (button) {
                if (button.disabled) return;
                closeActionMenus();
                const action = button.getAttribute('data-vault-explorer-toolbar-action') || '';
                if (!action || !currentSnapshot) return;
                callbacks.onAction?.(action, currentSnapshot, button);
                return;
            }
            const menuToggle = event.target?.closest?.('[data-vault-explorer-action-menu-toggle]');
            if (menuToggle) {
                if (menuToggle.disabled) return;
                toggleActionMenu(menuToggle);
                return;
            }
            const location = event.target?.closest?.('[data-vault-explorer-location]');
            if (!location || !currentSnapshot) return;
            callbacks.onLocation?.(
                location.getAttribute('data-vault-explorer-location') || '',
                currentSnapshot
            );
        }

        function toggleActionMenu(toggle) {
            const menuName = toggle.getAttribute('data-vault-explorer-action-menu-toggle') || '';
            const selector = `[data-vault-explorer-action-menu-options="${menuName}"]`;
            const options = container?.querySelector?.(selector)
                || selectionContainer?.querySelector?.(selector);
            if (!options) return;
            const opening = options.hidden;
            closeActionMenus();
            if (opening) {
                options.hidden = false;
                keepMenuInViewport(options);
                toggle.setAttribute('aria-expanded', 'true');
            }
        }

        function keepMenuInViewport(options) {
            if (!options.style || typeof options.getBoundingClientRect !== 'function') return;
            options.style.transform = '';
            const viewportWidth = window.innerWidth
                || window.document?.documentElement?.clientWidth
                || 0;
            if (!viewportWidth) return;
            const margin = 8;
            const bounds = options.getBoundingClientRect();
            let shift = 0;
            if (bounds.left < margin) {
                shift = margin - bounds.left;
            } else if (bounds.right > viewportWidth - margin) {
                shift = viewportWidth - margin - bounds.right;
            }
            if (shift) options.style.transform = `translateX(${Math.round(shift)}px)`;
        }

        function closeActionMenus() {
            for (const root of [container, selectionContainer]) {
                root?.querySelectorAll?.('[data-vault-explorer-action-menu-options]')
                    .forEach((options) => { options.hidden = true; });
                root?.querySelectorAll?.('[data-vault-explorer-action-menu-toggle]')
                    .forEach((toggle) => toggle.setAttribute('aria-expanded', 'false'));
            }
        }

        function handleDocumentClick(event) {
            if (
                container?.contains?.(event.target)
                || selectionContainer?.contains?.(event.target)
            ) return;
            closeActionMenus();
        }

        function handleKeydown(event) {
            if (event.key !== 'Escape') return;
            closeActionMenus();
        }

        function destroy() {
            container?.removeEventListener?.('click', handleClick);
            container?.removeEventListener?.('keydown', handleKeydown);
            locationContainer?.removeEventListener?.('click', handleClick);
            selectionContainer?.removeEventListener?.('click', handleClick);
            selectionContainer?.removeEventListener?.('keydown', handleKeydown);
            window.document?.removeEventListener?.('click', handleDocumentClick);
            if (container) container.innerHTML = '';
            if (locationContainer) locationContainer.innerHTML = '';
            if (selectionContainer) selectionContainer.innerHTML = '';
            container = null;
            locationContainer = null;
            selectionContainer = null;
            currentSnapshot = null;
        }

        return Object.freeze({ destroy, mount, render });
    }

    window.VaultExplorerToolbar = Object.freeze({
        create: createVaultExplorerToolbarController,
    });
})(window);
