(function explorerLayoutModule(window, document) {
    function create({ browserStorage, icons, callbacks }) {
        const surfaces = new Map();
        const desktop = window.matchMedia('(min-width: 1100px)');
        let mode = browserStorage.getItem('vaultExplorerLayout') === 'split' ? 'split' : 'overlay';
        const savedRatio = Number(browserStorage.getItem('vaultExplorerRatio'));
        let ratio = savedRatio > 0 && savedRatio < 1 ? savedRatio : 0.4;
        let dragging = false;
        let trackingView = false;

        function rememberView() {
            if (!trackingView) return;
            if (mode !== 'split' || !surfaces.size) {
                browserStorage.removeItem('vaultExplorerView');
                return;
            }
            const activeSurfaces = Array.from(surfaces.values());
            const vaultName = activeSurfaces.at(-1).vaultName;
            const view = { version: 1, vaultName, folder: '', ...callbacks.snapshotContext() };
            for (const surface of activeSurfaces) {
                if (surface.vaultName === vaultName) Object.assign(view, surface.snapshot());
            }
            browserStorage.setItem('vaultExplorerView', JSON.stringify(view));
        }

        function restorableView(vaults) {
            if (!desktop.matches || mode !== 'split' || surfaces.size) return null;
            let view;
            try {
                view = JSON.parse(browserStorage.getItem('vaultExplorerView'));
            } catch (error) {
                if (!(error instanceof SyntaxError)) throw error;
                browserStorage.removeItem('vaultExplorerView');
                return null;
            }
            if (view?.version !== 1 || typeof view.vaultName !== 'string'
                || !vaults.includes(view.vaultName)) return null;
            return {
                vaultName: view.vaultName,
                folder: typeof view.folder === 'string' ? view.folder : '',
                tab: ['chat', 'dashboard', 'configuration'].includes(view.tab) ? view.tab : 'chat',
                file: typeof view.file?.path === 'string' && view.file.path ? {
                    path: view.file.path,
                    initialMode: ['preview', 'edit', 'history'].includes(view.file.initialMode)
                        ? view.file.initialMode : 'preview',
                    initialRevisionId: typeof view.file.initialRevisionId === 'string'
                        ? view.file.initialRevisionId : '',
                } : null,
            };
        }
        window.addEventListener('pagehide', rememberView);
        const divider = document.createElement('div');
        divider.className = 'vault-explorer-divider';
        divider.hidden = true;
        divider.tabIndex = 0;
        divider.setAttribute('role', 'separator');
        divider.setAttribute('aria-label', 'Resize explorer');
        divider.setAttribute('aria-orientation', 'vertical');
        document.body.appendChild(divider);

        function isDocked(surface) {
            return desktop.matches && mode === 'split' && surfaces.size > 0
                && (!surface || surfaces.has(surface));
        }

        function bounds() {
            return { min: 360, max: Math.max(360, window.innerWidth - 480) };
        }

        function sync() {
            const docked = isDocked();
            const { min, max } = bounds();
            const width = Math.min(max, Math.max(min, window.innerWidth * ratio));
            document.documentElement.style.setProperty('--explorer-dock-width', `${width}px`);
            document.body.classList.toggle('explorer-docked', docked);
            divider.hidden = !docked;
            divider.setAttribute('aria-valuemin', String(min));
            divider.setAttribute('aria-valuemax', String(max));
            divider.setAttribute('aria-valuenow', String(Math.round(width)));
            divider.setAttribute('aria-valuetext', `${Math.round(width)} pixels wide`);
            if (!docked) endDrag();
            const topSurface = Array.from(surfaces.keys()).at(-1);
            for (const [overlay, { toggle }] of surfaces) {
                overlay.classList.toggle('vault-explorer-docked', docked);
                overlay.inert = overlay !== topSurface;
                const panel = overlay.querySelector('[role="dialog"]');
                if (docked) panel.removeAttribute('aria-modal');
                else panel.setAttribute('aria-modal', 'true');
                toggle.hidden = !desktop.matches;
                const label = docked ? 'Use overlay view' : 'Use split view';
                toggle.setAttribute('aria-label', label);
                toggle.setAttribute('aria-pressed', String(docked));
                toggle.title = label;
            }
            rememberView();
        }

        function resize(width) {
            const { min, max } = bounds();
            ratio = Math.min(max, Math.max(min, width)) / window.innerWidth;
            sync();
        }

        function endDrag() {
            if (!dragging) return;
            dragging = false;
            document.body.classList.remove('explorer-resizing');
            if (divider.hasPointerCapture(dividerPointer)) divider.releasePointerCapture(dividerPointer);
            browserStorage.setItem('vaultExplorerRatio', String(ratio));
        }

        let dividerPointer = 0;
        divider.addEventListener('pointerdown', (event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            dividerPointer = event.pointerId;
            dragging = true;
            divider.setPointerCapture(event.pointerId);
            document.body.classList.add('explorer-resizing');
        });
        divider.addEventListener('pointermove', (event) => {
            if (dragging) resize(window.innerWidth - event.clientX);
        });
        divider.addEventListener('pointerup', endDrag);
        divider.addEventListener('pointercancel', endDrag);
        divider.addEventListener('lostpointercapture', endDrag);
        divider.addEventListener('keydown', (event) => {
            const { min, max } = bounds();
            const width = window.innerWidth * ratio;
            const values = { ArrowLeft: width + 24, ArrowRight: width - 24, Home: min, End: max };
            if (!(event.key in values)) return;
            event.preventDefault();
            resize(values[event.key]);
            browserStorage.setItem('vaultExplorerRatio', String(ratio));
        });
        window.addEventListener('resize', sync);
        document.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape' || event.defaultPrevented || !isDocked()) return;
            if (event.target !== divider && !Array.from(surfaces.keys()).some((node) => node.contains(event.target))) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            if (canClose()) callbacks.close();
        });

        function attach(overlay, { vaultName, canClose = () => true, snapshot = () => ({}) }) {
            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.dataset.explorerLayoutToggle = 'true';
            toggle.className = 'ui-icon-button is-compact';
            toggle.innerHTML = icons.PANEL_RIGHT_ICON_SVG;
            const actions = overlay.querySelector('.app-modal-header .app-modal-actions');
            actions.insertBefore(toggle, actions.lastElementChild);
            trackingView = true;
            surfaces.set(overlay, { toggle, vaultName, canClose, snapshot });
            toggle.addEventListener('click', () => {
                mode = isDocked() ? 'overlay' : 'split';
                browserStorage.setItem('vaultExplorerLayout', mode);
                sync();
            });
            sync();
            return () => {
                surfaces.delete(overlay);
                sync();
            };
        }

        function canClose() {
            return Array.from(surfaces.values()).every((surface) => surface.canClose());
        }

        return {
            attach,
            restorableView,
            isDocked,
            canClose,
            vaultName: () => Array.from(surfaces.values()).at(-1)?.vaultName,
        };
    }
    window.VaultExplorerLayout = { create };
})(window, document);
