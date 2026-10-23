/** Selection-scoped instructions in the shared Markdown file preview. */
(function vaultSelectionPromptModule(window) {
    function formatBlock(path, text, prompt) {
        // Unusual paths use an encoded root-relative Markdown destination.
        const reference = /[\[\]|#\r\n]/.test(path)
            ? `[${path.replace(/[\\[\]]/g, '\\$&')}](</${path.split('/').map(encodeURIComponent).join('/')}>)`
            : `[[${path}]]`;
        const quote = text.replace(/\r\n?/g, '\n').split('\n')
            .map((line) => line ? `> ${line}` : '>').join('\n');
        return `${reference}\n\n${quote}${prompt.trim() ? `\n\n${prompt.trim()}` : ''}`;
    }

    function create({ overlay, preview, path, vault, icons, getContext, isActive, isLocked, onAppend, onSend, onAccepted }) {
        const document = overlay.ownerDocument;
        const lifetime = new AbortController();
        const { signal } = lifetime;
        const host = document.createElement('div');
        host.className = 'vault-selection-prompt';
        host.hidden = true;
        host.innerHTML = `
            <button type="button" class="ui-icon-button is-primary" data-quote-open aria-label="Ask about selected text" title="Ask about selected text" aria-expanded="false" aria-controls="vault-selection-panel">
                ${icons.MESSAGE_SQUARE_PLUS_ICON_SVG}
            </button>
            <section id="vault-selection-panel" class="vault-selection-panel" aria-label="Ask about selected text" hidden>
                <div class="vault-selection-heading"><span data-quote-path></span><button type="button" class="ui-icon-button is-compact" data-quote-dismiss aria-label="Discard selection prompt" title="Discard selection prompt">${icons.X_ICON_SVG}</button></div>
                <blockquote data-quote-text></blockquote>
                <label for="vault-selection-input">Instruction (optional)</label>
                <textarea id="vault-selection-input" rows="3" placeholder="What would you like to do with this passage?"></textarea>
                <div class="vault-selection-actions"><button type="button" class="ui-button-secondary" data-quote-append>Add to draft</button><button type="button" class="ui-button-primary" data-quote-send title="Send now (Ctrl+Enter or Cmd+Enter)">Send now</button></div>
            </section>
            <p class="vault-selection-feedback" role="status" aria-live="polite" hidden></p>`;
        overlay.appendChild(host);
        const open = host.querySelector('[data-quote-open]');
        const panel = host.querySelector('section');
        const input = host.querySelector('textarea');
        const feedback = host.querySelector('[role="status"]');
        const buttons = [host.querySelector('[data-quote-append]'), host.querySelector('[data-quote-send]')];
        let captured = null;
        let expanded = false;
        let sending = false;
        let sendRequestId = 0;
        let disposed = false;
        let feedbackTimer = null;
        let lastLockReason = '';

        function setFeedback(message) {
            feedback.textContent = message;
            feedback.hidden = !message;
        }

        function contextMatches() {
            const current = getContext();
            return captured && current.vault === vault && current.sessionId === captured.sessionId;
        }

        function blockedReason() {
            if (!contextMatches()) return 'Return to the original chat and vault to use this selection.';
            if (isLocked()) return 'Available when the active response or review finishes.';
            return '';
        }

        function syncLocks() {
            if (!captured) return;
            const reason = blockedReason();
            input.readOnly = sending;
            host.querySelector('[data-quote-dismiss]').disabled = sending;
            for (const button of buttons) {
                button.disabled = sending || Boolean(reason);
                button.title = reason || (button === buttons[1] ? 'Send now (Ctrl+Enter or Cmd+Enter)' : 'Add to draft');
            }
            if (reason) setFeedback(reason);
            else if (!sending && lastLockReason && feedback.textContent === lastLockReason) setFeedback('');
            lastLockReason = reason;
        }

        function position() {
            if (host.hidden || !captured) return;
            const viewport = window.visualViewport;
            const left = viewport?.offsetLeft || 0;
            const top = viewport?.offsetTop || 0;
            const width = viewport?.width || window.innerWidth;
            const height = viewport?.height || window.innerHeight;
            host.style.maxHeight = `${Math.max(80, height - 16)}px`;
            const rect = captured.range.getBoundingClientRect();
            const size = host.getBoundingClientRect();
            const x = Math.max(left + 8, Math.min(rect.left, left + width - size.width - 8));
            const below = rect.bottom + 8;
            const y = below + size.height < top + height - 8 ? below : rect.top - size.height - 8;
            host.style.left = `${x}px`;
            host.style.top = `${Math.max(top + 8, Math.min(y, top + height - size.height - 8))}px`;
        }

        function reset({ restoreFocus = false } = {}) {
            input.value = '';
            expanded = false;
            captured = null;
            panel.hidden = true;
            host.hidden = true;
            open.hidden = false;
            open.setAttribute('aria-expanded', 'false');
            host.classList.remove('is-expanded');
            setFeedback('');
            if (restoreFocus) preview.focus({ preventScroll: true });
        }

        function selectionChanged() {
            if (disposed || sending || expanded || input.value) return;
            if (!isActive()) { reset(); return; }
            const selection = document.getSelection();
            if (!selection || selection.isCollapsed || !selection.rangeCount) {
                if (!feedbackTimer) reset();
                return;
            }
            const range = selection.getRangeAt(0);
            if (!preview.contains(range.startContainer) || !preview.contains(range.endContainer)
                || !selection.toString().trim()) { reset(); return; }
            window.clearTimeout(feedbackTimer);
            feedbackTimer = null;
            open.hidden = false;
            setFeedback('');
            captured = { text: selection.toString(), range: range.cloneRange(), sessionId: getContext().sessionId };
            host.hidden = false;
            syncLocks();
            position();
        }

        function expand() {
            if (!captured) return;
            expanded = true;
            panel.hidden = false;
            open.hidden = true;
            open.setAttribute('aria-expanded', 'true');
            host.classList.add('is-expanded');
            host.querySelector('[data-quote-path]').textContent = path;
            host.querySelector('[data-quote-text]').textContent = captured.text;
            syncLocks();
            position();
            input.focus({ preventScroll: true });
        }

        async function act(send) {
            if (sending || !captured || blockedReason()) { syncLocks(); return; }
            const block = formatBlock(path, captured.text, input.value);
            if (!send) {
                if (onAppend(block) === false) return;
                reset({ restoreFocus: true });
                window.clearTimeout(feedbackTimer);
                setFeedback('Added to chat draft.');
                host.hidden = false;
                open.hidden = true;
                // A brief local acknowledgement; the next selection starts fresh.
                feedbackTimer = window.setTimeout(() => {
                    feedbackTimer = null;
                    if (!captured) reset();
                }, 1600);
                document.getSelection()?.removeAllRanges();
                return;
            }
            const requestId = ++sendRequestId;
            sending = true;
            syncLocks();
            setFeedback('Sending…');
            let accepted = false;
            try {
                await onSend(block, {
                    // First send allocates a session before starting its task.
                    onSessionAssigned: (sessionId) => { captured.sessionId = sessionId; },
                    onAccepted: () => {
                        accepted = true;
                        sending = false;
                        reset();
                        onAccepted();
                    },
                    onStartError: (error) => {
                        if (!disposed) setFeedback(error.message);
                    },
                });
            } finally {
                // A previous response may still be refreshing after the next send starts.
                if (requestId === sendRequestId) {
                    sending = false;
                    if (!disposed) {
                        syncLocks();
                        if (expanded && !accepted) input.focus({ preventScroll: true });
                    }
                }
            }
        }

        function canLeave() {
            if (sending) return false;
            return !input.value.trim() || window.confirm('Discard the unsent selection prompt?');
        }

        function deactivate() {
            if (!canLeave()) return false;
            reset();
            return true;
        }

        open.addEventListener('pointerdown', (event) => event.preventDefault(), { signal });
        open.addEventListener('click', expand, { signal });
        host.querySelector('[data-quote-dismiss]').addEventListener('click', () => reset({ restoreFocus: true }), { signal });
        buttons[0].addEventListener('click', () => { void act(false); }, { signal });
        buttons[1].addEventListener('click', () => { void act(true); }, { signal });
        document.addEventListener('selectionchange', selectionChanged, { signal });
        document.addEventListener('keydown', (event) => {
            if (host.hidden) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopImmediatePropagation();
                if (sending) return;
                if (expanded) {
                    expanded = false;
                    panel.hidden = true;
                    open.hidden = false;
                    host.classList.remove('is-expanded');
                    open.setAttribute('aria-expanded', 'false');
                    open.focus({ preventScroll: true });
                    position();
                } else if (canLeave()) reset({ restoreFocus: true });
            } else if (event.target === input && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                event.stopImmediatePropagation();
                void act(true);
            }
        }, { capture: true, signal });
        preview.addEventListener('pointerup', () => window.requestAnimationFrame(selectionChanged), { signal });
        document.addEventListener('scroll', position, { capture: true, signal });
        window.addEventListener('resize', position, { signal });
        window.visualViewport?.addEventListener('resize', position, { signal });
        window.visualViewport?.addEventListener('scroll', position, { signal });
        const resizeObserver = new ResizeObserver(position);
        resizeObserver.observe(host);
        preview.tabIndex = 0;
        return Object.freeze({
            canLeave, deactivate, syncLocks,
            destroy() {
                disposed = true;
                lifetime.abort();
                window.clearTimeout(feedbackTimer);
                resizeObserver.disconnect();
                host.remove();
            },
        });
    }
    window.VaultSelectionPrompt = Object.freeze({ create, formatBlock });
})(window);
