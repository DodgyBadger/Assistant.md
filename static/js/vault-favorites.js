(function vaultFavoritesModule(window) {
    async function read(vault, { signal } = {}) {
        return request(vault, { signal });
    }

    async function set(vault, paths, favorite) {
        const result = await request(vault, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths, favorite }),
        });
        window.document.dispatchEvent(new CustomEvent('vault-favorites-changed', {
            detail: { vault, paths: result },
        }));
        return result;
    }

    async function request(vault, options) {
        const response = await fetch(`api/vaults/${encodeURIComponent(vault)}/favorites`, {
            cache: 'no-store', ...options,
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.message || `HTTP ${response.status}`);
        }
        const payload = await response.json();
        return new Set(payload.paths);
    }

    window.VaultFavorites = Object.freeze({ read, set });
})(window);
