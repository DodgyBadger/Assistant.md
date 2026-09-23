(function chatThinkingModule(window, document) {
    function createChatThinkingController() {
        function renderAssistantThinking(context) {
            const thinking = context.thinkingText.trim();
            if (!thinking && context.thinkingDiv) {
                context.thinkingDiv.remove();
                context.thinkingDiv = null;
                context.thinkingTextSpan = null;
                context.thinkingToggle = null;
                return;
            }
            if (!thinking) {
                return;
            }
            if (!context.thinkingDiv) {
                context.thinkingDiv = document.createElement('div');
                context.thinkingDiv.className = 'assistant-thinking';
                context.contentDiv.insertBefore(context.thinkingDiv, context.bodyDiv);
            }
            const formattedThinking = formatThinkingText(thinking);
            if (!context.collapseThinking) {
                renderPlainAssistantThinking(context, formattedThinking);
                return;
            }
            renderCollapsibleAssistantThinking(context, formattedThinking);
        }

        function renderPlainAssistantThinking(context, text) {
            if (context.thinkingTextSpan && !context.thinkingToggle) {
                context.thinkingTextSpan.textContent = text;
                return;
            }
            context.thinkingDiv.innerHTML = '';
            context.thinkingDiv.className = 'assistant-thinking';

            const label = document.createElement('div');
            label.className = 'assistant-thinking-label';
            label.textContent = 'Reasoning';

            const textSpan = document.createElement('div');
            textSpan.className = 'assistant-thinking-text';
            textSpan.textContent = text;

            context.thinkingDiv.appendChild(label);
            context.thinkingDiv.appendChild(textSpan);
            context.thinkingTextSpan = textSpan;
            context.thinkingToggle = null;
        }

        function renderCollapsibleAssistantThinking(context, text) {
            ensureCollapsibleThinkingStructure(context);
            context.thinkingTextSpan.textContent = text;
            setThinkingExpanded(context, Boolean(context.thinkingExpanded));
        }

        function ensureCollapsibleThinkingStructure(context) {
            if (context.thinkingToggle && context.thinkingTextSpan) {
                return;
            }

            context.thinkingDiv.innerHTML = '';
            context.thinkingDiv.className = 'assistant-thinking assistant-thinking-collapsible';

            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'assistant-thinking-toggle';
            toggle.title = 'Show reasoning';

            const chevron = document.createElement('span');
            chevron.className = 'assistant-thinking-chevron';
            chevron.setAttribute('aria-hidden', 'true');
            chevron.textContent = '▸';

            const label = document.createElement('span');
            label.className = 'assistant-thinking-label';
            label.textContent = 'Reasoning';

            const textSpan = document.createElement('div');
            textSpan.className = 'assistant-thinking-text';

            toggle.appendChild(chevron);
            toggle.appendChild(label);
            toggle.addEventListener('click', () => {
                context.thinkingExpanded = !context.thinkingExpanded;
                setThinkingExpanded(context, context.thinkingExpanded);
            });

            context.thinkingDiv.appendChild(toggle);
            context.thinkingDiv.appendChild(textSpan);
            context.thinkingToggle = toggle;
            context.thinkingTextSpan = textSpan;
        }

        function setThinkingExpanded(context, expanded) {
            if (!context.thinkingDiv || !context.thinkingToggle) {
                return;
            }
            context.thinkingDiv.classList.toggle('is-expanded', expanded);
            context.thinkingDiv.classList.toggle('is-collapsed', !expanded);
            context.thinkingToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            context.thinkingToggle.title = expanded ? 'Hide reasoning' : 'Show reasoning';
            const chevron = context.thinkingToggle.querySelector('.assistant-thinking-chevron');
            if (chevron) {
                chevron.textContent = expanded ? '▾' : '▸';
            }
        }

        function formatThinkingText(text) {
            return String(text || '')
                .replace(/([.!?]["')\]]?)(?=[A-Z])/g, '$1 ')
                .replace(/[ \t]{2,}/g, ' ');
        }

        return Object.freeze({ render: renderAssistantThinking });
    }

    window.ChatThinking = Object.freeze({ create: createChatThinkingController });
})(window, document);
