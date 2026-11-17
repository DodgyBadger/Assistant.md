/** Private, ephemeral human controls for one task-owned browser page. */
(function browserHandoffModule(window, document) {
    const EDITING_KEYS = new Set([
        'Tab', 'Enter', 'Backspace', 'Delete', 'ArrowLeft', 'ArrowRight',
        'ArrowUp', 'ArrowDown', 'Home', 'End',
    ]);
    const ACTIVE = new Set(['waiting', 'human']);
    const bytes = (text) => new TextEncoder().encode(text).length;

    function create({ state }) {
        let active = null;

        function current(run) {
            return active === run && run.dialog.open && run.context.messageDiv.isConnected &&
                state.sessionId === run.sessionId &&
                run.context.browserHandoff?.handoff_id === run.view.handoff_id &&
                run.context.browserHandoff?.task_id === run.view.task_id &&
                run.context.browserHandoff?.page_id === run.view.page_id &&
                run.context.messageDiv.getClientRects().length > 0 &&
                ACTIVE.has(run.context.browserHandoff?.status);
        }

        function visible(run, epoch) {
            return current(run) && run.epoch === epoch && !document.hidden;
        }

        function clearPixels(run) {
            run.metadata = null;
            run.hasFrame = false;
            run.image.hidden = true;
            run.image.removeAttribute('src');
            for (const url of run.urls) URL.revokeObjectURL(url);
            run.urls.clear();
        }

        function discardInput(run) {
            run.queue.length = 0;
            run.selection = null;
            run.keyboard.value = '';
            run.composing = false;
        }

        function controls(run) {
            run.handback.disabled = run.returning || run.queue.length > 0 || !run.claimed || !run.hasFrame;
            run.keyboard.readOnly = !run.claimed || !run.hasFrame || run.returning;
            run.image.setAttribute('aria-disabled', String(run.returning || !run.metadata));
            const windowBusy = run.returning || run.busy || run.queue.length > 0 || !run.metadata;
            run.original.disabled = windowBusy || run.framePageId === run.view.page_id;
            run.signIn.disabled = windowBusy || !run.popupId || run.framePageId === run.popupId;
            run.original.setAttribute('aria-pressed', String(run.framePageId === run.view.page_id));
            run.signIn.setAttribute('aria-pressed', String(Boolean(run.popupId) && run.framePageId === run.popupId));
        }

        function close(run = active) {
            if (!run || active !== run) return;
            active = null;
            run.returning = false;
            run.epoch += 1;
            clearTimeout(run.timer);
            run.request?.abort();
            discardInput(run);
            clearPixels(run);
            run.observer?.disconnect();
            run.listeners.abort();
            run.dialog.close();
            run.dialog.remove();
            if (run.opener.isConnected) run.opener.focus();
        }

        async function request(run, suffix, init = {}) {
            const controller = new AbortController();
            run.request = controller;
            const deadline = setTimeout(() => controller.abort(), 10000);
            try {
                const response = await fetch(run.path + suffix, {
                    ...init, signal: controller.signal, cache: 'no-store',
                    credentials: 'same-origin', redirect: 'error',
                });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw { reason: data.detail, status: response.status, retire: data.requires_fresh_session === true };
                }
                // Keep the deadline and request owner through body consumption.
                return suffix === '/frame'
                    ? { headers: response.headers, blob: await response.blob() }
                    : await response.json();
            } finally {
                clearTimeout(deadline);
                if (run.request === controller) run.request = null;
            }
        }

        async function frame(run, epoch) {
            const result = await request(run, '/frame');
            if (!visible(run, epoch)) return false;
            const { headers, blob } = result;
            const origin = headers.get('X-Browser-Origin');
            let site;
            try { site = new URL(origin); } catch { throw { reason: 'invalid_frame', retire: true }; }
            if (!['http:', 'https:'].includes(site.protocol) || site.origin !== origin || origin.length > 512) {
                throw { reason: 'invalid_frame', retire: true };
            }
            const width = Number(headers.get('X-Browser-Width'));
            const height = Number(headers.get('X-Browser-Height'));
            const counter = Number(headers.get('X-Browser-Frame'));
            const sequence = Number(headers.get('X-Browser-Sequence'));
            const popupId = headers.get('X-Browser-Popup') || '';
            const windowKind = headers.get('X-Browser-Window');
            const selectionRevision = Number(headers.get('X-Browser-Selection'));
            const metadata = {
                page_id: headers.get('X-Browser-Page'),
                view_ref: headers.get('X-Browser-View'),
                input_ref: headers.get('X-Browser-Input'),
                sequence: sequence + 1,
            };
            if (blob.type !== 'image/jpeg' || blob.size > 2000000 ||
                !Number.isInteger(width) || width < 1 || width > 1600 ||
                !Number.isInteger(height) || height < 1 || height > 1200 ||
                !Number.isSafeInteger(counter) || counter <= (metadata.page_id === run.framePageId ? run.counter : 0) ||
                !Number.isSafeInteger(sequence) || sequence < 0 ||
                headers.get('X-Browser-Root') !== run.view.page_id ||
                (popupId && !/^[a-f0-9]{32}$/.test(popupId)) ||
                !Number.isSafeInteger(selectionRevision) || selectionRevision < run.selectionRevision ||
                !((windowKind === 'original' && metadata.page_id === run.view.page_id) ||
                  (windowKind === 'sign_in' && popupId && metadata.page_id === popupId)) ||
                !/^[a-f0-9]{32}$/.test(metadata.view_ref || '') ||
                !/^[a-f0-9]{32}$/.test(metadata.input_ref || '')) {
                throw { reason: 'invalid_frame', retire: true };
            }
            run.metadata = null;
            const url = URL.createObjectURL(blob);
            run.urls.add(url);
            const decoded = new Image();
            decoded.src = url;
            await decoded.decode();
            if (!visible(run, epoch)) return false;
            // Conceal replacement pixels without collapsing their layout box:
            // display:none would clamp the modal's scroll on every refresh.
            run.image.style.visibility = 'hidden';
            run.image.src = url;
            await run.image.decode();
            if (!visible(run, epoch)) return false;
            if (run.image.naturalWidth !== width || run.image.naturalHeight !== height) {
                throw { reason: 'invalid_frame', retire: true };
            }
            for (const previous of run.urls) {
                if (previous !== url) { URL.revokeObjectURL(previous); run.urls.delete(previous); }
            }
            if (run.framePageId && (run.framePageId !== metadata.page_id || run.selectionRevision !== selectionRevision)) {
                discardInput(run);
                run.notice = windowKind === 'sign_in'
                    ? 'Sign-in window selected. Check the destination before continuing.'
                    : 'Original page selected. Inspect it before continuing.';
            }
            const popupNotice = headers.get('X-Browser-Popup-Notice');
            if (popupNotice === 'unsupported_navigation') run.notice = 'This sign-in window could not be opened. Try another sign-in method.';
            if (popupNotice === 'popup_limit') run.notice = 'Only one sign-in window can be open at a time.';
            run.popupId = popupId;
            run.framePageId = metadata.page_id;
            run.selectionRevision = selectionRevision;
            run.windowControls.hidden = !popupId;
            run.windowLabel.textContent = windowKind === 'sign_in' ? 'Sign-in window' : 'Original page';
            if (run.origin && run.origin !== origin) {
                discardInput(run);
                run.notice = 'The website changed. Check the destination before continuing.';
            }
            run.origin = origin;
            run.originLabel.textContent = origin;
            run.width = width;
            run.height = height;
            run.counter = counter;
            run.metadata = metadata;
            run.hasFrame = true;
            run.image.hidden = false;
            run.image.style.visibility = '';
            return true;
        }

        function unavailable(run, error) {
            // Never display response bodies, URLs, SDK errors or private input.
            const status = Number.isInteger(error?.status) && error.status >= 400 && error.status <= 599
                ? `HTTP ${error.status}`
                : error?.reason === 'invalid_frame' ? 'invalid preview'
                    : error?.reason === 'stale_handoff' ? 'handoff changed' : 'connection or preview failure';
            run.context.browserHandoffNotice.textContent = `Browser unavailable (${run.claimed ? 'preview' : 'claim'}: ${status}). Reopen to check its state.`;
            close(run);
        }

        async function pump(run) {
            if (!current(run)) { close(run); return; }
            if (document.hidden || run.busy) return;
            if (run.returning) { void completeHandback(run); return; }
            clearTimeout(run.timer);
            const epoch = run.epoch;
            run.busy = true;
            controls(run);
            try {
                if (!run.claimed) {
                    const view = await request(run, '/claim', { method: 'POST' });
                    if (!visible(run, epoch)) return;
                    if (view.handoff_id !== run.view.handoff_id || view.task_id !== run.view.task_id ||
                        view.page_id !== run.view.page_id || view.status !== 'human') {
                        throw { reason: 'stale_handoff', retire: true };
                    }
                    run.claimed = true;
                }
                if (run.selection) {
                    const target = run.selection;
                    run.selection = null;
                    const selected = await request(run, '/page/' + encodeURIComponent(target), { method: 'POST' });
                    if (!visible(run, epoch)) return;
                    if (selected.page_id !== target || !Number.isSafeInteger(selected.selection_revision) ||
                        selected.selection_revision < run.selectionRevision) throw { reason: 'stale_input' };
                    run.selectionRevision = selected.selection_revision;
                }
                if (!await frame(run, epoch)) return;
                while (visible(run, epoch) && !run.returning && run.queue.length) {
                    const command = run.queue.shift();
                    const metadata = command.metadata || run.metadata;
                    if (!metadata) break;
                    // Coordinate intent carries the displayed frame at enqueue.
                    // Never silently retarget it after a later image/input.
                    if (command.metadata && (metadata.input_ref !== run.metadata?.input_ref ||
                        metadata.view_ref !== run.metadata?.view_ref)) {
                        discardInput(run);
                        run.notice = 'The page changed. Check it before trying again.';
                        break;
                    }
                    const { metadata: _reference, ...values } = command;
                    run.metadata = null;
                    controls(run);
                    try {
                        await request(run, '/input', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ ...metadata, ...values }),
                        });
                    } catch (error) {
                        // Even a failed response may follow a real native effect.
                        // Clear dependent commands and never replay this input.
                        discardInput(run);
                        run.notice = 'Input was not confirmed. Inspect the page before continuing.';
                        throw error;
                    }
                    if (!visible(run, epoch)) return;
                    if (!await frame(run, epoch)) return;
                }
                if (visible(run, epoch) && !run.returning) run.status.textContent = run.notice || 'Complete the human step, then return control.';
            } catch (error) {
                if (!visible(run, epoch)) return;
                discardInput(run);
                run.metadata = null;
                if (error.retire || !['browser_busy', 'frame_unavailable', 'stale_input', 'input_uncertain'].includes(error.reason)) {
                    unavailable(run, error);
                    return;
                }
                run.status.textContent = run.notice || 'Waiting for the browser. No input will be retried.';
            } finally {
                run.busy = false;
                if (!current(run)) close(run);
                else {
                    controls(run);
                    if (!document.hidden && run.returning) void completeHandback(run);
                    else if (!document.hidden) run.timer = setTimeout(() => pump(run), 600);
                }
            }
        }

        function enqueue(run, command) {
            if (!current(run) || document.hidden || !run.claimed || !run.hasFrame || run.returning) return;
            if (command.kind === 'text') {
                if (!command.text) return;
                if (bytes(command.text) > 4096) {
                    run.status.textContent = 'Enter at most 4096 bytes at a time.';
                    return;
                }
                const last = run.queue[run.queue.length - 1];
                if (last?.kind === 'text' && bytes(last.text + command.text) <= 4096) {
                    last.text += command.text;
                    controls(run);
                    return;
                }
            }
            if (run.queue.length >= 16) {
                discardInput(run);
                run.status.textContent = 'Input was too fast. Inspect the page before continuing.';
                return;
            }
            run.notice = '';
            run.queue.push(command);
            controls(run);
            if (!run.busy) void pump(run);
        }

        function handback(run) {
            if (!current(run) || document.hidden || run.returning || run.queue.length || !run.claimed || !run.hasFrame) return;
            clearTimeout(run.timer);
            // One explicit intent waits behind already-owned browser I/O.
            // Editing stops immediately; the backend still decides handback.
            run.returning = true;
            discardInput(run);
            run.status.textContent = 'Returning control after the current browser update…';
            controls(run);
            if (!run.busy) void completeHandback(run);
        }

        async function completeHandback(run) {
            if (!current(run) || document.hidden || !run.returning || run.busy) return;
            const epoch = run.epoch;
            run.busy = true;
            controls(run);
            try {
                await request(run, '/return', { method: 'POST' });
                if (visible(run, epoch)) close(run);
            } catch (error) {
                if (!visible(run, epoch)) return;
                run.notice = error.reason === 'credentials_pending'
                    ? 'Submit or clear password and verification fields before returning control.'
                    : 'Control could not be returned. Inspect the page and try again.';
                run.status.textContent = run.notice;
            } finally {
                run.busy = run.returning = false;
                if (current(run)) { controls(run); void pump(run); }
            }
        }

        function open(context, opener) {
            close();
            const view = context.browserHandoff;
            if (!ACTIVE.has(view?.status) || !state.sessionId || !context.messageDiv.isConnected) return;
            const dialog = document.createElement('dialog');
            dialog.className = 'browser-handoff-modal';
            dialog.setAttribute('aria-label', 'Browser');
            // All markup is fixed; website content is rendered only as pixels.
            dialog.innerHTML = `<header class="app-modal-header"><h2>Browser</h2><div class="browser-handoff-actions"><button type="button" class="ui-button-primary" data-return disabled>Return control</button><button type="button" class="ui-button-secondary" data-close>Close</button></div></header>
                <div class="browser-handoff-body"><div data-window-controls hidden><span data-window-label></span>
                <button type="button" class="ui-button-secondary" data-original>Original page</button>
                <button type="button" class="ui-button-secondary" data-sign-in>Sign-in window</button></div>
                <output aria-label="Website origin" class="cell-mono"></output><p role="status">Opening browser…</p>
                <div class="browser-handoff-preview" role="group" aria-label="Browser page controls" tabindex="0"><img alt="Browser page" draggable="false" hidden></div>
                <label>Keyboard input<input type="password" aria-label="Browser keyboard input" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="Type or paste into the focused page field" readonly></label>
                <p class="text-xs text-txt-secondary">Click the page to focus a field, then type or paste. Tab moves within the page; Shift+Tab leaves keyboard input. Escape closes this window; you can reopen it from chat.</p>
                </div>`;
            document.body.appendChild(dialog);
            const run = {
                context, opener, view, dialog, sessionId: state.sessionId,
                path: `api/chat/sessions/${encodeURIComponent(state.sessionId)}/browser-handoff/${encodeURIComponent(view.handoff_id)}`,
                listeners: new AbortController(), request: null, urls: new Set(), queue: [],
                epoch: 0, counter: 0, metadata: null, claimed: false, hasFrame: false,
                selection: null, selectionRevision: 0, popupId: '', framePageId: '',
                windowControls: dialog.querySelector('[data-window-controls]'),
                windowLabel: dialog.querySelector('[data-window-label]'),
                original: dialog.querySelector('[data-original]'), signIn: dialog.querySelector('[data-sign-in]'),
                busy: false, returning: false, composing: false, notice: '',
                preview: dialog.querySelector('.browser-handoff-preview'),
                image: dialog.querySelector('img'), keyboard: dialog.querySelector('input'),
                originLabel: dialog.querySelector('output'),
                status: dialog.querySelector('[role=status]'), handback: dialog.querySelector('[data-return]'),
            };
            active = run;
            const on = (target, name, listener, options = {}) => target.addEventListener(name, listener, { ...options, signal: run.listeners.signal });
            on(dialog.querySelector('[data-close]'), 'click', () => close(run));
            on(dialog, 'cancel', (event) => { event.preventDefault(); close(run); });
            on(run.handback, 'click', () => { void handback(run); });
            const selectPage = (pageId) => {
                if (!current(run) || run.returning || run.busy || run.queue.length || !run.metadata) return;
                discardInput(run);
                clearPixels(run);
                run.selection = pageId;
                controls(run);
                void pump(run);
            };
            on(run.original, 'click', () => selectPage(run.view.page_id));
            on(run.signIn, 'click', () => { if (run.popupId) selectPage(run.popupId); });
            on(run.image, 'click', (event) => {
                if (!run.metadata) {
                    run.status.textContent = 'Wait for the page to update, then try again.';
                    return;
                }
                const bounds = run.image.getBoundingClientRect();
                const x = Math.floor((event.clientX - bounds.left) * run.width / bounds.width);
                const y = Math.floor((event.clientY - bounds.top) * run.height / bounds.height);
                if (x < 0 || y < 0 || x >= run.width || y >= run.height) return;
                enqueue(run, { kind: 'click', x, y, metadata: { ...run.metadata } });
                run.preview.focus({ preventScroll: true });
            });
            on(run.image, 'wheel', (event) => {
                event.preventDefault();
                if (!run.metadata) {
                    run.status.textContent = 'Wait for the page to update, then try again.';
                    return;
                }
                const delta = Math.max(-800, Math.min(800, Math.round(event.deltaY)));
                if (delta) enqueue(run, { kind: 'scroll', delta_y: delta, metadata: { ...run.metadata } });
            }, { passive: false });
            on(run.keyboard, 'compositionstart', () => { run.composing = true; });
            const textInput = () => {
                if (run.composing) return;
                const text = run.keyboard.value;
                run.keyboard.value = '';
                enqueue(run, { kind: 'text', text });
            };
            on(run.keyboard, 'compositionend', () => { run.composing = false; textInput(); });
            on(run.keyboard, 'input', textInput);
            const editingKey = (event) => {
                if (run.composing || event.isComposing) return;
                const selectAll = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a' && !event.altKey && !event.shiftKey;
                if (selectAll || (EDITING_KEYS.has(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey)) {
                    event.preventDefault();
                    enqueue(run, { kind: 'key', key: selectAll ? 'SelectAll' : event.key });
                }
            };
            on(run.keyboard, 'keydown', editingKey);
            on(run.preview, 'keydown', (event) => {
                if (!current(run) || document.hidden || run.returning || !run.hasFrame) return;
                const printable = event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
                const paste = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v' && !event.altKey;
                const composition = ['Process', 'Dead', 'Unidentified', 'Compose'].includes(event.key);
                if (printable || paste || composition) {
                    // Keep non-text clicks on the page. On actual typing,
                    // native default input targets this private field without
                    // scrolling the viewer or synthesizing/replaying the key.
                    run.keyboard.focus({ preventScroll: true });
                    return;
                }
                editingKey(event);
            });
            on(document, 'visibilitychange', () => {
                run.returning = false;
                run.epoch += 1;
                discardInput(run);
                clearPixels(run);
                run.request?.abort();
                controls(run);
                if (!document.hidden) void pump(run);
            });
            // DOM removal/chat switching invalidates even an in-flight fetch.
            run.observer = new MutationObserver(() => { if (!current(run)) close(run); });
            run.observer.observe(document.body, {
                childList: true, subtree: true, attributes: true,
                attributeFilter: ['class', 'style', 'hidden'],
            });
            on(window, 'pagehide', () => close(run));
            dialog.showModal();
            void pump(run);
        }

        function render(context, payload) {
            const view = payload.browser_handoff;
            if (!view || !/^[a-f0-9]{32}$/.test(view.handoff_id || '') ||
                !/^task_[a-f0-9]{32}$/.test(view.task_id || '') ||
                !/^[a-f0-9]{32}$/.test(view.page_id || '')) return;
            context.browserHandoff = view;
            if (!context.browserHandoffButton) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'ui-button-primary';
                button.addEventListener('click', () => open(context, button));
                context.artifactList.appendChild(button);
                context.browserHandoffButton = button;
                const notice = document.createElement('span');
                notice.setAttribute('role', 'status');
                context.artifactList.appendChild(notice);
                context.browserHandoffNotice = notice;
            }
            context.browserHandoffButton.textContent = ACTIVE.has(view.status) ? 'Open browser' : 'Browser handoff ended';
            context.browserHandoffButton.disabled = !ACTIVE.has(view.status);
            if (active?.context === context &&
                (active.view.handoff_id !== view.handoff_id || !ACTIVE.has(view.status))) close(active);
        }

        function finish(context) {
            if (active?.context === context) close(active);
            if (context.browserHandoffButton) context.browserHandoffButton.disabled = true;
        }

        return Object.freeze({ render, finish });
    }
    window.BrowserHandoff = Object.freeze({ create });
})(window, document);
