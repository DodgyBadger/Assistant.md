(function vaultExplorerDestinationModule(window) {
    function createVaultExplorerDestinationController({ onChange = null } = {}) {
        let mode = null;

        function begin({
            initialPath = '',
            purpose = '',
            sourceKind = '',
            sourcePath = '',
        } = {}) {
            if (!purpose) throw new TypeError('A destination purpose is required.');
            mode = {
                path: normalizePath(initialPath, { allowRoot: true }),
                purpose,
                sourceKind,
                sourcePath: sourcePath
                    ? normalizePath(sourcePath, { allowRoot: false })
                    : '',
            };
            validateDestination(mode.path);
            emitChange();
            return snapshot();
        }

        function select(path) {
            if (!mode) throw new TypeError('Destination selection is not active.');
            const normalized = normalizePath(path, { allowRoot: true });
            validateDestination(normalized);
            if (normalized === mode.path) return snapshot();
            mode = { ...mode, path: normalized };
            emitChange();
            return snapshot();
        }

        function cancel() {
            if (!mode) return;
            mode = null;
            emitChange();
        }

        function snapshot() {
            return Object.freeze(mode
                ? { active: true, ...mode }
                : {
                    active: false,
                    path: '',
                    purpose: '',
                    sourceKind: '',
                    sourcePath: '',
                });
        }

        function validateDestination(path) {
            if (
                mode?.sourceKind === 'directory'
                && (path === mode.sourcePath || path.startsWith(`${mode.sourcePath}/`))
            ) {
                throw new TypeError('A folder cannot be moved into itself.');
            }
        }

        function emitChange() {
            if (typeof onChange === 'function') onChange(snapshot());
        }

        return Object.freeze({ begin, cancel, select, snapshot });
    }

    function normalizePath(path, { allowRoot }) {
        const raw = String(path || '').trim().replace(/\\/g, '/');
        const parts = raw.split('/').filter((part) => part && part !== '.');
        if (
            parts.includes('..')
            || parts.some((part) => /[\u0000-\u001f\u007f]/.test(part))
        ) {
            throw new TypeError('Path must be a safe vault-relative path.');
        }
        const normalized = parts.join('/');
        if (!normalized && !allowRoot) {
            throw new TypeError('Path must be a non-empty vault-relative path.');
        }
        return normalized;
    }

    window.VaultExplorerDestination = Object.freeze({
        create: createVaultExplorerDestinationController,
    });
})(window);
