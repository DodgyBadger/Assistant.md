(function vaultExplorerSearchModule(window) {
    function createVaultExplorerSearchController({ utils }) {
        const { escapeHtml } = utils;
        let abortController = null;
        let generation = 0;

        async function search({ vault, query, path = '', limit = 100 }) {
            cancel();
            abortController = new AbortController();
            const params = new URLSearchParams({ query, limit: String(limit) });
            if (path) params.set('path', path);
            const response = await fetch(
                `api/vaults/${encodeURIComponent(vault)}/content-search?${params}`,
                { signal: abortController.signal }
            );
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            return response.json();
        }

        function render(payload, { selectable = true, selectedPaths = [], supportsPath }) {
            const selected = new Set(selectedPaths);
            const files = new Map();
            for (const match of payload.matches || []) {
                if (!files.has(match.path)) files.set(match.path, []);
                files.get(match.path).push(match);
            }
            return Array.from(files.entries()).map(([path, matches]) => `
                <div data-vault-path-picker-row="${escapeHtml(path)}" data-vault-path-picker-depth="0">
                    <div class="workspace-tree-row vault-content-search-row${selected.has(path) ? ' is-selected' : ''}" role="treeitem">
                        ${selectable ? `<input type="checkbox" class="vault-explorer-row-selection"
                            data-vault-explorer-select-item data-path="${escapeHtml(path)}"
                            data-kind="file" data-import-eligible="${supportsPath(path)}"
                            aria-label="Select ${escapeHtml(path)}" ${selected.has(path) ? 'checked' : ''} />` : ''}
                        <button type="button" class="workspace-tree-select"
                            data-vault-path-picker-select="${escapeHtml(path)}" data-vault-path-picker-kind="file">
                            <span class="workspace-tree-label min-w-0">
                                <span class="workspace-tree-name">${escapeHtml(path)}</span>
                                ${matches.map((match) => `
                                    <span class="file-reference-path">Line ${Number(match.line)} · ${escapeHtml(match.snippet)}</span>
                                `).join('')}
                            </span>
                        </button>
                    </div>
                    <div class="workspace-tree-children hidden" data-vault-path-picker-children></div>
                </div>
            `).join('');
        }

        async function load({
            overlay,
            vault,
            query,
            path = '',
            selectable = false,
            selectedPaths = [],
            supportsPath,
            setStatus,
            onRendered,
        }) {
            const results = overlay.querySelector('[data-vault-path-picker-results]');
            if (!(results instanceof HTMLElement)) return;
            if (!query) {
                cancel();
                setStatus('Enter text to search file contents.');
                results.innerHTML = '<p class="text-sm text-txt-secondary">Content matches will appear here.</p>';
                return;
            }
            setStatus('Searching contents...');
            results.innerHTML = '';
            const pendingSearch = search({ vault, query, path });
            const currentGeneration = generation;
            const payload = await pendingSearch;
            if (currentGeneration !== generation || !overlay.isConnected) return;
            const matches = Array.isArray(payload.matches) ? payload.matches : [];
            const suffix = payload.truncated
                ? ' Showing the first matches; refine your search.'
                : '';
            setStatus(
                matches.length
                    ? `Found ${matches.length} content match${matches.length === 1 ? '' : 'es'}.${suffix}`
                    : 'No matching content.'
            );
            results.innerHTML = matches.length
                ? render(payload, { selectable, selectedPaths, supportsPath })
                : '<p class="text-sm text-txt-secondary">No matching content.</p>';
            onRendered?.();
        }

        function cancel() {
            abortController?.abort();
            abortController = null;
            generation += 1;
        }

        return Object.freeze({ cancel, load, render, search });
    }

    window.VaultExplorerSearch = Object.freeze({
        create: createVaultExplorerSearchController,
    });
})(window);
