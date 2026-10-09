(function fileReferenceLinksModule(window, document) {
    function createFileReferenceLinksController({ callbacks }) {
        const resolutionCache = new Map();

        function selectedVault() {
            return callbacks.selectedVault();
        }

        function workspacePath() {
            return callbacks.workspacePath();
        }

        function openFile(path, options) {
            callbacks.openFile(path, options);
        }

        function enhanceFileLinks(container, context = {}) {
            if (!container) return;
            context = {
                ...context,
                vaultName: context.vaultName || selectedVault(),
                workspacePath: workspacePath(),
            };
            container.querySelectorAll('[data-vault-wikilink]').forEach((element) => {
                if (element.closest('a, pre, code')) return;
                const candidate = localPathCandidate(element.dataset.vaultWikilink, context, { wikilink: true });
                if (candidate) {
                    element.dataset.vaultReferenceCandidate = candidate;
                    element.dataset.vaultReferenceRoot = 'true';
                    element.dataset.vaultReferenceLabel = element.textContent;
                }
            });
            markStandaloneCandidates(container);
            const textNodes = [];
            const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
                acceptNode(node) {
                    const parent = node.parentElement;
                    if (!parent || parent.closest('a, button, code, pre, textarea, [data-vault-reference-candidate], [data-vault-wikilink]')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return candidateMatches(node.textContent || '').length
                        ? NodeFilter.FILTER_ACCEPT
                        : NodeFilter.FILTER_REJECT;
                },
            });
            while (walker.nextNode()) {
                textNodes.push(walker.currentNode);
            }
            textNodes.forEach(markTextNodeCandidates);
            container.querySelectorAll('a[href]').forEach((link) => {
                if (!(link instanceof HTMLAnchorElement)) return;
                if (link.dataset.vaultFileEnhanced === 'true') return;
                if (link.dataset.vaultReferenceCandidate) return;
                const href = link.getAttribute('href') || '';
                if (!href || /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(href)) return;
                const candidate = localPathCandidate(href, context);
                if (!candidate) {
                    link.replaceWith(document.createTextNode(link.textContent || href));
                    return;
                }
                link.dataset.vaultReferenceCandidate = candidate;
                link.dataset.vaultReferenceRoot = 'true';
                link.dataset.vaultReferenceLabel = link.textContent;
                // Never let pending local links navigate into application routes.
                link.setAttribute('href', '#');
                link.removeAttribute('target');
                link.addEventListener('click', (event) => event.preventDefault());
            });
            return resolveMarkedCandidates(container, context).catch((error) => {
                console.error('Unable to resolve vault references:', error);
            });
        }

        function markStandaloneCandidates(container) {
            container.querySelectorAll('code').forEach((code) => {
                if (!(code instanceof HTMLElement) || code.closest('pre')) return;
                if (code.dataset.vaultFileEnhanced === 'true') return;
                const candidate = standaloneCandidate(code.textContent || '');
                if (candidate) code.dataset.vaultReferenceCandidate = candidate;
            });
        }

        function markTextNodeCandidates(node) {
            const text = node.textContent || '';
            const matches = candidateMatches(text);
            if (!matches.length) return;
            let cursor = 0;
            const fragment = document.createDocumentFragment();
            matches.forEach(({ start, end, raw, candidate }) => {
                if (start > cursor) {
                    fragment.appendChild(document.createTextNode(text.slice(cursor, start)));
                }
                const marker = document.createElement('span');
                marker.textContent = raw;
                marker.dataset.vaultReferenceCandidate = candidate;
                fragment.appendChild(marker);
                cursor = end;
            });
            if (cursor < text.length) {
                fragment.appendChild(document.createTextNode(text.slice(cursor)));
            }
            node.parentNode?.replaceChild(fragment, node);
        }

        async function resolveMarkedCandidates(container, context) {
            const marked = Array.from(
                container.querySelectorAll('[data-vault-reference-candidate]')
            ).filter((element) => element instanceof HTMLElement);
            if (!marked.length) return;
            for (const vaultRoot of [true, false]) {
                const group = marked.filter((element) =>
                    (element.dataset.vaultReferenceRoot === 'true') === vaultRoot
                );
                const paths = group.map((element) => element.dataset.vaultReferenceCandidate || '');
                const resolutions = await resolveCandidates(paths, context, vaultRoot);
                group.forEach((element) => {
                    if (!container.contains(element)) return;
                    const candidate = element.dataset.vaultReferenceCandidate || '';
                    const resolution = resolutions.get(candidate);
                    delete element.dataset.vaultReferenceCandidate;
                    if (!resolution || resolution.kind === 'missing') {
                        if (element instanceof HTMLAnchorElement) {
                            element.replaceWith(document.createTextNode(element.textContent || candidate));
                        }
                        return;
                    }
                    const link = document.createElement('a');
                    link.href = '#';
                    link.className = element.tagName === 'CODE'
                        ? 'vault-file-link vault-file-link-code'
                        : 'vault-file-link';
                    link.textContent = element.dataset.vaultReferenceLabel ?? `@${resolution.path}`;
                    link.dataset.vaultFileEnhanced = 'true';
                    link.dataset.vaultFilePath = resolution.path;
                    link.dataset.vaultFileKind = resolution.kind;
                    link.title = resolution.kind === 'directory'
                        ? `Browse ${resolution.path}`
                        : `Open ${resolution.path}`;
                    const vaultName = context.vaultName || selectedVault();
                    const browseDirectory = () => callbacks.openExplorer({
                        vaultName,
                        revealPath: resolution.path,
                    });
                    link.addEventListener('click', (event) => {
                        event.preventDefault();
                        if (resolution.kind === 'directory') {
                            browseDirectory();
                        } else {
                            openFile(resolution.path, { vaultName, onBack: browseDirectory });
                        }
                    });
                    element.replaceWith(link);
                });
            }
        }

        async function resolveCandidates(paths, context, vaultRoot) {
            const vault = context.vaultName || selectedVault();
            const workspace = vaultRoot || context.sourcePath ? '' : context.workspacePath;
            const normalized = [...new Set(paths.filter(Boolean))];
            const resolved = new Map();
            const unresolved = [];
            normalized.forEach((path) => {
                const cached = resolutionCache.get(resolutionCacheKey(vault, workspace, path));
                if (cached) {
                    resolved.set(path, cached);
                } else {
                    unresolved.push(path);
                }
            });
            if (!vault || !unresolved.length) return resolved;
            const response = await fetch(
                `api/vaults/${encodeURIComponent(vault)}/file-refs/resolve`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paths: unresolved, workspace_path: workspace }),
                }
            );
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            const payload = await response.json();
            const items = Array.isArray(payload.items) ? payload.items : [];
            items.forEach((item) => {
                const requestedPath = item.requested_path || '';
                if (!requestedPath) return;
                resolved.set(requestedPath, item);
                if (item.kind !== 'missing') {
                    resolutionCache.set(
                        resolutionCacheKey(vault, workspace, requestedPath),
                        item
                    );
                }
            });
            return resolved;
        }

        function resolutionCacheKey(vault, workspace, path) {
            return `${vault}\u0000${workspace}\u0000${path}`;
        }

        function localPathCandidate(value, context, { wikilink = false } = {}) {
            let path = String(value || '').trim();
            if (!wikilink) {
                try { path = decodeURIComponent(path.split(/[?#]/, 1)[0]); }
                catch { return ''; }
            }
            if (!path || /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(path) || /[\x00-\x1f\\]/.test(path)) return '';
            const legacyRoot = !wikilink && path.startsWith('@');
            if (legacyRoot) path = path.slice(1);
            const parts = !wikilink && !legacyRoot && !path.startsWith('/') && context.sourcePath
                ? context.sourcePath.split('/').slice(0, -1) : [];
            for (const part of path.split('/')) {
                if (!part || part === '.') continue;
                if (part === '..') {
                    if (!parts.length) return '';
                    parts.pop();
                } else parts.push(part);
            }
            const candidate = parts.join('/');
            return candidate.length <= 1000 ? candidate : '';
        }

        function candidateMatches(text) {
            const patterns = [
                {
                    regex: /@([^@\n<>()\[\]{};:?]*?\.(?:md|markdown|txt))/gi,
                    group: 0,
                    priority: 0,
                },
                {
                    regex: /@((?:[\w .,-]+\/)+[\w.@-]+\/?)/gi,
                    group: 0,
                    priority: 0,
                },
                {
                    regex: /(^|[\s([`])([\w.@-]+\/[\w .@/-]+\.(?:md|markdown|txt))(?=$|[\s).,;:`\]])/gi,
                    group: 2,
                    priority: 1,
                },
                {
                    regex: /(^|[\s([`])([\w.@-]+(?:\/[\w.@-]+(?: [\w.@-]+)*)+\/?)(?=$|[\s).,;:`\]])/g,
                    group: 2,
                    priority: 2,
                },
                {
                    regex: /(^|[\s([`])([\w.@-]+(?: [\w.@-]+)*\/)(?=$|[\s).,;:`\]])/g,
                    group: 2,
                    priority: 3,
                },
            ];
            const found = [];
            patterns.forEach(({ regex, group, priority }) => {
                let match;
                while ((match = regex.exec(text)) !== null) {
                    const raw = match[group] || '';
                    const start = match.index + (group === 2 ? (match[1] || '').length : 0);
                    const candidate = normalizeDisplayPath(raw);
                    if (candidate) {
                        found.push({ start, end: start + raw.length, raw, candidate, priority });
                    }
                }
            });
            found.sort((left, right) => left.start - right.start || left.priority - right.priority);
            const selected = [];
            let cursor = -1;
            found.forEach((match) => {
                if (match.start < cursor) return;
                selected.push(match);
                cursor = match.end;
            });
            return selected;
        }

        function standaloneCandidate(value) {
            const original = String(value || '').trim();
            const raw = normalizeDisplayPath(original);
            if (!raw || /^https?:\/\//i.test(raw) || raw.startsWith('#')) {
                return '';
            }
            if (
                !original.startsWith('@')
                && !/\.(md|markdown|txt)$/i.test(raw)
                && !raw.includes('/')
            ) {
                return '';
            }
            return raw;
        }

        function normalizeDisplayPath(value) {
            return String(value || '')
                .trim()
                .replace(/^@/, '')
                .replace(/^\.?\//, '')
                .replace(/[),.;:]+$/, '')
                .replace(/\/+$/, '');
        }

        return Object.freeze({ enhanceFileLinks });
    }

    window.FileReferenceLinks = Object.freeze({
        create: createFileReferenceLinksController,
    });
})(window, document);
