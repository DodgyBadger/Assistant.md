(function chatMessageControlsModule(window, document) {
    function createChatMessageControls({ state, elements, icons, utils, markdown, callbacks }) {
        function addLoadingMessage() {
            const messageDiv = document.createElement('div');
            messageDiv.className = 'flex justify-start';
            messageDiv.id = 'loading-message';

            const contentDiv = document.createElement('div');
            contentDiv.className = 'max-w-[80%] px-4 py-2 rounded-lg message-bubble message-assistant';
            contentDiv.innerHTML = `<div class="flex items-center space-x-2 text-sm">
                <span class="typing-indicator inline-flex">${icons.TYPING_DOTS_HTML}</span>
                <span class="ml-1">Contacting assistant…</span>
            </div>`;

            messageDiv.appendChild(contentDiv);
            callbacks.appendMessageNode(messageDiv, { forceScroll: true });
            return messageDiv;
        }

        function removeLoadingMessage(messageDiv) {
            if (messageDiv && messageDiv.parentNode) {
                messageDiv.parentNode.removeChild(messageDiv);
            }
        }

        function addMessage(role, content, options = {}) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `flex ${role === 'user' ? 'justify-end' : 'justify-start'}`;

            const contentDiv = document.createElement('div');
            contentDiv.className = `max-w-[80%] px-4 py-2 rounded-lg message-bubble ${
                role === 'user'
                    ? 'message-user'
                    : role === 'error'
                    ? 'message-error'
                    : 'message-assistant prose prose-sm max-w-none'
            }`;

            const bodyDiv = document.createElement('div');
            bodyDiv.className = 'message-body';

            if (role === 'assistant') {
                markdown.renderHtml(bodyDiv, content);
                markdown.postProcess(bodyDiv);
            } else {
                const escapedContent = content
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/\n/g, '<br>');
                bodyDiv.innerHTML = escapedContent;
            }

            contentDiv.appendChild(bodyDiv);

            const footerDiv = document.createElement('div');
            footerDiv.className = 'message-footer';

            const footerContent = document.createElement('div');
            footerContent.className = 'message-footer-content';

            if (role === 'assistant' && options.footerHtml) {
                footerContent.innerHTML = options.footerHtml;
            }

            const actionsDiv = document.createElement('div');
            actionsDiv.className = 'message-footer-actions';

            if (role === 'user' || role === 'error') {
                footerDiv.classList.add('message-footer-right');
            }

            actionsDiv.appendChild(createCopyButton(
                () => utils.getCopyableText(bodyDiv),
                'message-copy-button'
            ));
            const forkButton = role === 'assistant' ? createForkButton(options.sequenceIndex) : null;
            if (forkButton) {
                actionsDiv.appendChild(forkButton);
            }

            if (footerContent.innerHTML.trim()) {
                footerDiv.appendChild(footerContent);
            } else {
                footerDiv.classList.add('message-footer-right');
            }

            footerDiv.appendChild(actionsDiv);
            contentDiv.appendChild(footerDiv);
            messageDiv.appendChild(contentDiv);
            callbacks.appendMessageNode(messageDiv, { forceScroll: true });
        }

        function createCopyButton(getText, extraClass = '') {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `copy-button ${extraClass}`.trim();
            button.setAttribute('aria-label', 'Copy to clipboard');
            button.title = 'Copy to clipboard';
            button.innerHTML = icons.COPY_ICON_SVG;

            button.addEventListener('click', async (event) => {
                event.stopPropagation();
                const text = getText();
                if (!text) {
                    utils.flashCopyFeedback(button, false);
                    return;
                }
                const didCopy = await utils.handleCopy(text);
                utils.flashCopyFeedback(button, didCopy);
            });

            return button;
        }

        function createForkButton(sequenceIndex) {
            if (!Number.isInteger(sequenceIndex) || sequenceIndex < 0) {
                return null;
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'copy-button message-fork-button';
            button.setAttribute('aria-label', 'Fork session from this message');
            button.title = 'Fork session from this message';
            button.innerHTML = icons.FORK_ICON_SVG;

            button.addEventListener('click', async (event) => {
                event.stopPropagation();
                await forkCurrentSession(sequenceIndex, button);
            });

            return button;
        }

        async function forkCurrentSession(sequenceIndex, button) {
            const vault = elements.vaultSelector.value;
            const sessionId = state.sessionId;
            if (state.isLoading || !vault || !sessionId || !Number.isInteger(sequenceIndex)) {
                return;
            }

            const previousDisabled = button.disabled;
            button.disabled = true;
            try {
                const response = await fetch(`api/chat/sessions/${encodeURIComponent(sessionId)}/fork`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        vault_name: vault,
                        through_sequence_index: sequenceIndex
                    })
                });
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.message || `HTTP ${response.status}`);
                }
                const payload = await response.json();
                const forkSessionId = payload?.session?.session_id;
                if (!forkSessionId) {
                    throw new Error('Fork response did not include a new session id.');
                }
                state.sessionId = forkSessionId;
                await callbacks.fetchSessions(vault, forkSessionId);
                await callbacks.loadSession(forkSessionId);
            } catch (error) {
                console.error('Failed to fork chat session:', error);
                callbacks.addErrorMessage(`Fork failed: ${error.message}`);
                button.disabled = previousDisabled;
            }
        }

        return Object.freeze({
            addLoadingMessage,
            removeLoadingMessage,
            addMessage,
            createCopyButton,
            createForkButton,
        });
    }

    window.ChatMessageControls = Object.freeze({
        create: createChatMessageControls,
    });
})(window, document);
