(function vaultExplorerStateModule(window) {
    const FILE_OPERATIONS = ['open', 'edit', 'history'];
    const SINGLE_ITEM_OPERATIONS = ['copy', 'rename'];

    function createVaultExplorerStateController({ onChange = null } = {}) {
        let activeFolder = '';
        let selectedItems = new Map();
        let features = {
            batchMove: false,
            batchDelete: false,
        };

        function setActiveFolder(path) {
            const normalized = normalizePath(path, { allowRoot: true });
            if (normalized === activeFolder) return;
            activeFolder = normalized;
            emitChange();
        }

        function select(item) {
            const normalized = normalizeItem(item);
            const current = selectedItems.get(normalized.path);
            if (current && sameItem(current, normalized)) return;
            selectedItems.set(normalized.path, normalized);
            emitChange();
        }

        function deselect(path) {
            const normalized = normalizePath(path);
            if (!selectedItems.delete(normalized)) return;
            emitChange();
        }

        function toggle(item) {
            const normalized = normalizeItem(item);
            if (selectedItems.has(normalized.path)) {
                selectedItems.delete(normalized.path);
            } else {
                selectedItems.set(normalized.path, normalized);
            }
            emitChange();
        }

        function clearSelection() {
            if (!selectedItems.size) return;
            selectedItems.clear();
            emitChange();
        }

        function retainSelection(paths) {
            const retained = new Set(
                Array.from(paths || [], (path) => normalizePath(path))
            );
            const nextItems = new Map(
                Array.from(selectedItems.entries()).filter(([path]) => retained.has(path))
            );
            if (sameSelection(selectedItems, nextItems)) return;
            selectedItems = nextItems;
            emitChange();
        }

        function setFeatures(nextFeatures = {}) {
            const next = {
                batchMove: nextFeatures.batchMove === undefined
                    ? features.batchMove
                    : Boolean(nextFeatures.batchMove),
                batchDelete: nextFeatures.batchDelete === undefined
                    ? features.batchDelete
                    : Boolean(nextFeatures.batchDelete),
            };
            if (
                next.batchMove === features.batchMove
                && next.batchDelete === features.batchDelete
            ) return;
            features = next;
            emitChange();
        }

        function reset({ activeFolder: nextActiveFolder = '' } = {}) {
            activeFolder = normalizePath(nextActiveFolder, { allowRoot: true });
            selectedItems = new Map();
            features = {
                batchMove: false,
                batchDelete: false,
            };
            emitChange();
        }

        function restore(savedSnapshot) {
            const restoredFolder = normalizePath(
                savedSnapshot?.activeFolder || '',
                { allowRoot: true }
            );
            const restoredItems = new Map();
            for (const item of savedSnapshot?.selectedItems || []) {
                const normalized = normalizeItem(item);
                restoredItems.set(normalized.path, normalized);
            }
            activeFolder = restoredFolder;
            selectedItems = restoredItems;
            emitChange();
        }

        function snapshot() {
            const items = Array.from(selectedItems.values(), (item) => (
                Object.freeze({ ...item })
            ));
            const hasAncestorConflict = selectionHasAncestorConflict(items);
            const destinationPath = destinationFor(activeFolder, items);
            return Object.freeze({
                activeFolder,
                destinationPath,
                selectedCount: items.length,
                selectedItems: Object.freeze(items),
                selectedPaths: Object.freeze(items.map((item) => item.path)),
                hasAncestorConflict,
                operations: operationStates({
                    activeFolder,
                    destinationPath,
                    features,
                    hasAncestorConflict,
                    items,
                }),
            });
        }

        function emitChange() {
            if (typeof onChange === 'function') onChange(snapshot());
        }

        return Object.freeze({
            clearSelection,
            deselect,
            reset,
            restore,
            retainSelection,
            select,
            setActiveFolder,
            setFeatures,
            snapshot,
            toggle,
        });
    }

    function operationStates({ destinationPath, features, hasAncestorConflict, items }) {
        const count = items.length;
        const single = count === 1 ? items[0] : null;
        const singleFile = single?.kind === 'file';
        const singleDirectory = single?.kind === 'directory';
        const hasSelection = count > 0;
        const allImportEligible = hasSelection && items.every((item) => (
            item.kind === 'file' && item.importEligible
        ));
        const batchConflictReason = hasAncestorConflict
            ? 'A selected folder contains another selected item.'
            : '';
        const operations = {
            new_file: folderDestinationState(count, singleDirectory, destinationPath),
            new_directory: folderDestinationState(count, singleDirectory, destinationPath),
            upload: folderDestinationState(count, singleDirectory, destinationPath),
            import_url: folderDestinationState(count, singleDirectory, destinationPath),
            refresh: enabled(),
            clear: hasSelection ? enabled() : disabled('Nothing is selected.'),
            open: single ? enabled() : disabled('Select one file or folder.'),
            edit: singleFile ? enabled() : disabled('Select one file.'),
            history: singleFile ? enabled() : disabled('Select one file.'),
            reference: hasSelection ? enabled() : disabled('Select one or more items.'),
            copy: single ? enabled() : disabled('Select one item.'),
            workspace: singleDirectory ? enabled() : disabled('Select one folder.'),
            rename: single ? enabled() : disabled('Select one item.'),
            import_file: allImportEligible
                ? enabled()
                : disabled('Select only files supported for Markdown import.'),
            move: mutationState({
                operation: 'move',
                count,
                batchEnabled: features.batchMove,
                batchConflictReason,
            }),
            delete: mutationState({
                operation: 'delete',
                count,
                batchEnabled: features.batchDelete,
                batchConflictReason,
            }),
        };
        for (const operation of FILE_OPERATIONS) {
            if (!singleFile && operations[operation].enabled) {
                operations[operation] = disabled('Select one file.');
            }
        }
        for (const operation of SINGLE_ITEM_OPERATIONS) {
            if (!single && operations[operation].enabled) {
                operations[operation] = disabled('Select one item.');
            }
        }
        return Object.freeze(operations);
    }

    function folderDestinationState(count, singleDirectory, destinationPath) {
        if (count === 0 || singleDirectory) {
            return enabled({ destinationPath });
        }
        return disabled('Select one folder or clear the selection.');
    }

    function mutationState({ operation, count, batchEnabled, batchConflictReason }) {
        if (!count) return disabled(`Select an item to ${operation}.`);
        if (count === 1) return enabled();
        if (batchConflictReason) return disabled(batchConflictReason);
        if (!batchEnabled) return disabled(`Batch ${operation} is unavailable.`);
        return enabled();
    }

    function enabled(details = {}) {
        return Object.freeze({ enabled: true, reason: '', ...details });
    }

    function disabled(reason) {
        return Object.freeze({ enabled: false, reason });
    }

    function destinationFor(activeFolder, items) {
        if (items.length === 1 && items[0].kind === 'directory') {
            return items[0].path;
        }
        return items.length === 0 ? activeFolder : '';
    }

    function selectionHasAncestorConflict(items) {
        const directories = items.filter((item) => item.kind === 'directory');
        return directories.some((directory) => items.some((item) => (
            item.path !== directory.path
            && item.path.startsWith(`${directory.path}/`)
        )));
    }

    function normalizeItem(item) {
        if (!item || typeof item !== 'object') {
            throw new TypeError('A selected item is required.');
        }
        const path = normalizePath(item.path);
        const kind = item.kind === 'directory'
            ? 'directory'
            : item.kind === 'file'
                ? 'file'
                : '';
        if (!kind) throw new TypeError('A selected item must be a file or directory.');
        return Object.freeze({
            path,
            kind,
            importEligible: Boolean(item.importEligible),
        });
    }

    function normalizePath(path, { allowRoot = false } = {}) {
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

    function sameItem(left, right) {
        return left.path === right.path
            && left.kind === right.kind
            && left.importEligible === right.importEligible;
    }

    function sameSelection(left, right) {
        if (left.size !== right.size) return false;
        return Array.from(left.entries()).every(([path, item]) => (
            sameItem(item, right.get(path) || {})
        ));
    }

    window.VaultExplorerState = Object.freeze({
        create: createVaultExplorerStateController,
    });
})(window);
