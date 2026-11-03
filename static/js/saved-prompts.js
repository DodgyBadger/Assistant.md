(function savedPromptsModule(window, document) {
    function create({ state, elements, composer, callbacks }) {
        const trigger = elements.sendBtn;
        const menu = elements.savedPromptsMenu;
        const send = elements.sendBtn;
        let generation = 0;
        let request = null;
        let opened = null;
        let gesture = null;
        let consumeClick = false;

        function scope() {
            return JSON.stringify([
                elements.vaultSelector?.value || '', state.sessionId || '',
                elements.workspacePathInput?.value.trim() || '',
            ]);
        }

        function locked() {
            return Boolean(state.isLoading || state.isCancellingChat || state.pendingDeferredReview
                || state.chatModeSavePending || elements.chatInput.disabled);
        }

        function cancelGesture(consume = false) {
            if (!gesture) return;
            window.clearTimeout(gesture.timer);
            gesture = null;
            if (consume) consumeClick = true;
        }

        function close({ restoreFocus = false } = {}) {
            ++generation;
            request?.abort();
            request = null;
            opened = null;
            menu.hidden = true;
            menu.replaceChildren();
            trigger.setAttribute('aria-expanded', 'false');
            cancelGesture(true);
            if (restoreFocus) trigger.focus();
        }

        function status(text) {
            const message = document.createElement('div');
            message.className = 'saved-prompts-status';
            message.setAttribute('role', 'status');
            message.textContent = text;
            menu.replaceChildren(message);
        }

        function positionMenu() {
            const anchor = trigger.getBoundingClientRect();
            const above = Math.max(0, anchor.top - 16);
            const below = Math.max(0, window.innerHeight - anchor.bottom - 16);
            const placeAbove = above >= below;
            menu.style.maxHeight = `${Math.min(384, placeAbove ? above : below)}px`;
            menu.style.left = `${Math.max(8, Math.min(anchor.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8))}px`;
            menu.style.top = placeAbove ? 'auto' : `${anchor.bottom + 8}px`;
            menu.style.bottom = placeAbove ? `${window.innerHeight - anchor.top + 8}px` : 'auto';
        }

        async function open() {
            if (locked() || !elements.vaultSelector.value) return;
            close();
            const input = elements.chatInput;
            const current = {
                scope: scope(), draft: input.value, caret: input.selectionStart ?? input.value.length,
                generation: generation,
            };
            opened = current;
            menu.hidden = false;
            trigger.setAttribute('aria-expanded', 'true');
            status('Loading saved prompts…');
            positionMenu();
            request = new AbortController();
            const params = new URLSearchParams({ workspace_path: elements.workspacePathInput?.value.trim() || '' });
            try {
                const response = await fetch(`api/vaults/${encodeURIComponent(elements.vaultSelector.value)}/saved-prompts?${params}`, {
                    signal: request.signal, cache: 'no-store',
                });
                if (!response.ok) throw new Error('Saved prompts could not be loaded.');
                const payload = await response.json();
                if (generation !== current.generation || scope() !== current.scope || locked()) return;
                const prompts = Array.isArray(payload.prompts) ? payload.prompts : [];
                if (!prompts.length) {
                    status('No saved prompts found for this workspace.');
                    return;
                }
                menu.replaceChildren();
                for (const prompt of prompts) {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.setAttribute('role', 'menuitem');
                    button.textContent = prompt.title;
                    button.addEventListener('click', () => {
                        if (locked() || scope() !== current.scope || generation !== current.generation
                            || input.value !== current.draft) {
                            close();
                            return;
                        }
                        const caret = current.caret;
                        close();
                        composer.insertSavedPrompt(prompt.body, caret);
                    });
                    menu.appendChild(button);
                }
                positionMenu();
                menu.querySelector('[role="menuitem"]')?.focus({ preventScroll: true });
            } catch (error) {
                if (error.name !== 'AbortError' && generation === current.generation) {
                    status('Saved prompts could not be loaded.');
                }
            }
        }

        function syncControls() {
            if (locked()) {
                trigger.removeAttribute('aria-haspopup');
                trigger.removeAttribute('aria-controls');
                trigger.removeAttribute('aria-keyshortcuts');
            } else {
                trigger.setAttribute('aria-haspopup', 'menu');
                trigger.setAttribute('aria-controls', menu.id);
                trigger.setAttribute('aria-keyshortcuts', 'Alt+ArrowDown');
            }
            if (opened && (locked() || opened.scope !== scope())) close();
            if (gesture && (locked() || gesture.scope !== scope())) cancelGesture(true);
        }

        function handleSendClick(event) {
            const consumed = consumeClick && event.detail !== 0;
            consumeClick = false;
            cancelGesture();
            if (consumed) {
                event.preventDefault();
                return;
            }
            close();
            if (state.isLoading) callbacks.stop();
            else callbacks.send();
        }

        send.addEventListener('keydown', (event) => {
            if (event.altKey && event.key === 'ArrowDown' && !locked()) {
                event.preventDefault();
                if (opened) close({ restoreFocus: true });
                else void open();
            }
        });
        send.addEventListener('pointerdown', (event) => {
            if (event.button !== 0 || !event.isPrimary) return;
            consumeClick = false;
            cancelGesture();
            if (locked()) return;
            gesture = { id: event.pointerId, x: event.clientX, y: event.clientY, scope: scope() };
            send.setPointerCapture?.(event.pointerId);
            gesture.timer = window.setTimeout(() => {
                if (!gesture || gesture.scope !== scope() || locked()) return;
                consumeClick = true;
                gesture = null;
                void open();
            }, 500);
        });
        send.addEventListener('pointermove', (event) => {
            if (gesture && Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) > 10) {
                cancelGesture(true);
            }
        });
        send.addEventListener('pointerup', () => cancelGesture());
        send.addEventListener('pointercancel', () => cancelGesture(true));
        send.addEventListener('lostpointercapture', () => cancelGesture(true));
        send.addEventListener('contextmenu', (event) => {
            if (consumeClick || opened) event.preventDefault();
        });
        menu.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const buttons = [...menu.querySelectorAll('[role="menuitem"]')];
            const index = buttons.indexOf(document.activeElement);
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
                : (index + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length;
            buttons[next]?.focus();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && opened) {
                event.preventDefault();
                event.stopImmediatePropagation();
                close({ restoreFocus: true });
            }
        }, true);
        document.addEventListener('click', (event) => {
            if (opened && !menu.contains(event.target) && !send.contains(event.target)) close();
        }, true);
        window.addEventListener('resize', () => { if (opened) positionMenu(); });
        window.addEventListener('blur', () => { cancelGesture(true); close(); });
        return Object.freeze({ close, handleSendClick, isOpen: () => Boolean(opened), syncControls });
    }
    window.SavedPrompts = Object.freeze({ create });
})(window, document);
