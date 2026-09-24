(function vaultExplorerBatchMovesModule(window) {
    function createVaultExplorerBatchMovesController({ icons, utils, callbacks }) {
        const { escapeHtml } = utils;
        let busy = false;

        function show(overlay, items, initialDestination = '') {
            const panel = overlay.querySelector('[data-vault-explorer-action-panel]');
            if (!(panel instanceof HTMLElement)) return;
            const workspace = callbacks.workspacePath();
            panel.innerHTML = `
                <div class="vault-explorer-action-header">
                    <strong>Move ${items.length} items</strong>
                    <button type="button" class="ui-icon-button is-compact" data-vault-explorer-action-cancel aria-label="Cancel" title="Cancel">${icons.X_ICON_SVG}</button>
                </div>
                <form class="vault-explorer-mutation-form vault-explorer-move-form"
                    data-vault-explorer-mutation-form data-operation="batch_move"
                    data-sources="${escapeHtml(JSON.stringify(items))}">
                    <p class="text-txt-secondary">Choose one destination folder in the tree for every selected item.</p>
                    <div class="vault-explorer-move-destination">
                        <span>Destination</span>
                        <strong class="cell-mono" data-vault-explorer-move-destination>${escapeHtml(initialDestination || 'Vault root')}</strong>
                        ${workspace ? `<button type="button" class="ui-button-secondary" data-vault-explorer-move-shortcut="${escapeHtml(workspace)}">Workspace root</button>` : ''}
                        <button type="button" class="ui-button-secondary" data-vault-explorer-move-shortcut="">Vault root</button>
                    </div>
                    <div class="vault-explorer-form-actions">
                        <button type="button" class="ui-button-secondary" data-vault-explorer-action-cancel>Cancel</button>
                        <button type="submit" class="ui-button-primary">Move here</button>
                    </div>
                    <div class="text-sm" data-vault-explorer-form-status></div>
                </form>`;
            panel.classList.remove('hidden');
            try {
                callbacks.beginDestinationMode(overlay, {
                    initialPath: initialDestination,
                    purpose: 'batch_move',
                    sourceItems: items,
                });
            } catch (_) {
                initialDestination = '';
                const label = panel.querySelector('[data-vault-explorer-move-destination]');
                if (label) label.textContent = 'Vault root';
                callbacks.beginDestinationMode(overlay, {
                    initialPath: '',
                    purpose: 'batch_move',
                    sourceItems: items,
                });
            }
            panel.querySelectorAll('[data-vault-explorer-move-shortcut]').forEach((button) => {
                button.addEventListener('click', () => {
                    callbacks.selectDestination(
                        overlay,
                        button.getAttribute('data-vault-explorer-move-shortcut') || ''
                    );
                });
            });
        }

        async function submit(overlay, form, options) {
            const status = form.querySelector('[data-vault-explorer-form-status]');
            const submitButton = form.querySelector('button[type="submit"]');
            if (busy || callbacks.isReadOnly(options) || typeof options.onBatchMove !== 'function') {
                return;
            }
            busy = true;
            callbacks.syncInteractionLocks();
            if (submitButton instanceof HTMLButtonElement) submitButton.disabled = true;
            if (status) status.textContent = 'Moving selected items…';
            try {
                const items = JSON.parse(form.dataset.sources || '[]');
                const result = await options.onBatchMove({
                    sources: items.map((item) => item.path),
                    destination: callbacks.destinationSnapshot().path,
                });
                if (!callbacks.isActiveOverlay(overlay)) return;
                callbacks.batchMutationCompleted(result?.results || []);
                callbacks.closeActionPanel(overlay, { restoreFocus: false });
                try {
                    await callbacks.refreshExplorer(
                        overlay,
                        options,
                        result?.results?.[0]?.destination || ''
                    );
                } catch (refreshError) {
                    callbacks.setStatus(
                        overlay,
                        `The move succeeded, but the Explorer could not refresh: ${refreshError.message}`,
                        true
                    );
                }
            } catch (error) {
                if (!callbacks.isActiveOverlay(overlay)) return;
                if (status) {
                    status.innerHTML = `<span class="state-error">${escapeHtml(error.message)}</span>`;
                }
                if (submitButton instanceof HTMLButtonElement) submitButton.disabled = false;
            } finally {
                busy = false;
                if (callbacks.isActiveOverlay(overlay)) callbacks.syncInteractionLocks();
            }
        }

        function isBusy() {
            return busy;
        }

        function reset() {
            busy = false;
        }

        return Object.freeze({ isBusy, reset, show, submit });
    }

    window.VaultExplorerBatchMoves = Object.freeze({
        create: createVaultExplorerBatchMovesController,
    });
})(window);
