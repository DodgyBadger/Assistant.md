(function chatExcerptsModule(window, document) {
    function create({ utils, icons }) {
        let active = null;

        function open(button, body) {
            const paragraphs = Array.from(body.children)
                .map(block => block.innerText.trim()).filter(Boolean);
            if (!paragraphs.length && body.innerText.trim()) paragraphs.push(body.innerText.trim());
            if (!paragraphs.length) return;
            active?.close();
            const events = new AbortController();
            const overlay = document.createElement('div');
            overlay.className = 'app-modal-overlay chat-excerpt-overlay';
            overlay.innerHTML = `
                <section class="app-modal-panel chat-excerpt-panel" role="dialog" aria-modal="true" aria-labelledby="chat-excerpt-title">
                    <header class="app-modal-header">
                        <h2 id="chat-excerpt-title" class="text-lg font-semibold text-txt-primary">Copy excerpt</h2>
                        <button type="button" class="ui-icon-button" data-excerpt-close aria-label="Close">${icons.X_ICON_SVG}</button>
                    </header>
                    <div class="chat-excerpt-instructions">Tap the first paragraph, then the last. Scroll between them as needed.</div>
                    <div class="chat-excerpt-paragraphs" data-excerpt-paragraphs></div>
                    <footer class="chat-excerpt-footer">
                        <span role="status" aria-live="polite" data-excerpt-status>Choose a paragraph to begin.</span>
                        <button type="button" class="ui-button-secondary" data-excerpt-cancel>Cancel</button>
                        <button type="button" class="ui-button-primary" data-excerpt-copy disabled>Copy selected</button>
                    </footer>
                </section>`;
            let start = null;
            let end = null;
            let rangeComplete = false;
            let pending = false;
            const list = overlay.querySelector('[data-excerpt-paragraphs]');
            const copy = overlay.querySelector('[data-excerpt-copy]');
            const status = overlay.querySelector('[data-excerpt-status]');
            const choices = [];

            function close() {
                events.abort();
                overlay.remove();
                if (active?.overlay === overlay) active = null;
                if (button.isConnected) button.focus({ preventScroll: true });
            }

            function choose(index) {
                if (pending) return;
                if (start === null || rangeComplete) {
                    start = end = index;
                    rangeComplete = false;
                } else {
                    end = index;
                    rangeComplete = true;
                }
                const first = Math.min(start, end);
                const last = Math.max(start, end);
                choices.forEach((choice, i) => choice.setAttribute('aria-pressed', String(i >= first && i <= last)));
                copy.disabled = false;
                status.textContent = `${last - first + 1} paragraph${last === first ? '' : 's'} selected.`;
            }

            paragraphs.forEach((text, index) => {
                const choice = document.createElement('button');
                choice.type = 'button';
                choice.className = 'chat-excerpt-paragraph';
                choice.setAttribute('aria-label', `Paragraph ${index + 1}`);
                choice.setAttribute('aria-pressed', 'false');
                choice.setAttribute('aria-describedby', `chat-excerpt-paragraph-${index}`);
                const number = document.createElement('span');
                number.className = 'chat-excerpt-number';
                number.setAttribute('aria-hidden', 'true');
                number.textContent = index + 1;
                const content = document.createElement('span');
                content.id = `chat-excerpt-paragraph-${index}`;
                content.className = 'chat-excerpt-text';
                content.textContent = text;
                choice.append(number, content);
                choice.addEventListener('click', () => choose(index));
                list.appendChild(choice);
                choices.push(choice);
            });
            overlay.querySelector('[data-excerpt-close]').addEventListener('click', close);
            overlay.querySelector('[data-excerpt-cancel]').addEventListener('click', close);
            overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
            copy.addEventListener('click', async () => {
                if (start === null || pending) return;
                pending = true;
                copy.disabled = true;
                const text = paragraphs.slice(Math.min(start, end), Math.max(start, end) + 1).join('\n\n');
                const copied = await utils.handleCopy(text);
                if (active?.overlay !== overlay) return;
                if (copied) close();
                else {
                    pending = false;
                    copy.disabled = false;
                    status.textContent = 'Could not copy the excerpt. Try again.';
                    copy.focus();
                }
            });
            document.addEventListener('keydown', event => {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    close();
                } else if (event.key === 'Tab') {
                    const buttons = Array.from(overlay.querySelectorAll('button:not([disabled])'));
                    event.preventDefault();
                    const current = buttons.indexOf(document.activeElement);
                    const next = (current + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length;
                    buttons[next]?.focus();
                }
            }, { capture: true, signal: events.signal });
            active = { overlay, close };
            document.body.appendChild(overlay);
            overlay.querySelector('[data-excerpt-close]').focus({ preventScroll: true });
        }

        function attach(button, body) {
            let timer = null;
            let point = null;
            let consumed = false;
            function cancel(consume = false) {
                const armed = point !== null;
                window.clearTimeout(timer);
                timer = null;
                point = null;
                if (consume && armed) consumed = true;
            }
            button.title = 'Copy to clipboard (hold or Alt+ArrowDown to choose an excerpt)';
            button.setAttribute('aria-haspopup', 'dialog');
            button.setAttribute('aria-keyshortcuts', 'Alt+ArrowDown');
            button.addEventListener('pointerdown', event => {
                if (event.button !== 0 || !event.isPrimary) return;
                cancel();
                consumed = false;
                point = { x: event.clientX, y: event.clientY };
                button.setPointerCapture?.(event.pointerId);
                timer = window.setTimeout(() => {
                    cancel();
                    consumed = true;
                    if (button.isConnected) open(button, body);
                }, 500);
            });
            button.addEventListener('pointermove', event => {
                if (point && Math.hypot(event.clientX - point.x, event.clientY - point.y) > 10) cancel(true);
            });
            button.addEventListener('pointerup', () => cancel());
            button.addEventListener('pointercancel', () => cancel(true));
            button.addEventListener('lostpointercapture', () => cancel(true));
            button.addEventListener('contextmenu', event => event.preventDefault());
            button.addEventListener('keydown', event => {
                if (event.altKey && event.key === 'ArrowDown') {
                    event.preventDefault();
                    cancel();
                    open(button, body);
                }
            });
            return event => {
                const result = consumed && event.detail !== 0;
                consumed = false;
                return result;
            };
        }
        return Object.freeze({ attach });
    }
    window.ChatExcerpts = Object.freeze({ create });
})(window, document);
