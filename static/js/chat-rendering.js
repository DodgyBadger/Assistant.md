(function chatRenderingModule(window, document) {
    const CHAT_EMPTY_STATE_MESSAGE = 'Start a conversation...';

    function createChatRenderingController({ state, elements, icons, utils, callbacks }) {
        let currentEmptyStateMessage = CHAT_EMPTY_STATE_MESSAGE;
        let workspaceEditorOpen = false;
        const persistedToolEntriesById = new Map();
        const toolDetails = window.ChatToolDetails.create({
            state,
            elements,
            icons,
            utils,
            callbacks: {
                createCopyButton,
                formatToolElapsed,
                renderEditProposalArtifact: callbacks.renderEditProposalArtifact,
                toolStateLabel,
            },
        });
        const markdown = window.ChatMarkdown.create({
            utils,
            callbacks: {
                attachCodeCopyButtons,
                enhanceFileLinks: callbacks.enhanceFileLinks,
                scrollChatToBottom: callbacks.scrollChatToBottom,
            },
        });

        function isChatPlaceholderNode(node) {
            if (!node || !(node instanceof HTMLElement)) return false;
            return node.classList.contains('chat-start-panel') ||
                (
                    node.classList.contains('text-center') &&
                    node.classList.contains('text-txt-secondary') &&
                    node.classList.contains('text-sm')
                );
        }

        function clearChatPlaceholderIfPresent() {
            const container = elements.chatMessages;
            if (!container) return;
            if (container.children.length !== 1) return;
            if (!isChatPlaceholderNode(container.children[0])) return;
            container.innerHTML = '';
        }

        function appendChatMessageNode(node, { forceScroll = true } = {}) {
            const container = elements.chatMessages;
            if (!container || !node) return;
            clearChatPlaceholderIfPresent();
            container.appendChild(node);
            callbacks.scrollChatToBottom(forceScroll);
        }

        function renderChatEmptyState(message = CHAT_EMPTY_STATE_MESSAGE) {
            const container = elements.chatMessages;
            if (!container) return;
            toolDetails.close();
            persistedToolEntriesById.clear();
            currentEmptyStateMessage = message;
            container.innerHTML = '';
            if (message === CHAT_EMPTY_STATE_MESSAGE) {
                renderChatStartPanel(container);
                state.shouldAutoScroll = true;
                return;
            }
            const placeholder = document.createElement('div');
            placeholder.className = 'text-center text-txt-secondary text-sm';
            placeholder.textContent = message;
            container.appendChild(placeholder);
            state.shouldAutoScroll = true;
        }

        function refreshEmptyState() {
            const container = elements.chatMessages;
            if (!container || container.children.length !== 1 || !isChatPlaceholderNode(container.children[0])) {
                return;
            }
            renderChatEmptyState(currentEmptyStateMessage);
        }

        function renderChatStartPanel(container) {
            const modelText = selectedOptionText(elements.modelSelector) || 'No model selected';
            const thinkingText = selectedOptionText(elements.thinkingSelector).replace(/^Thinking:\s*/i, '') || 'Default';
            const workspacePath = (elements.workspacePathInput?.value || '').trim();

            const panel = document.createElement('div');
            panel.className = 'chat-start-panel';
            panel.innerHTML = `
                <div class="chat-start-panel-title">Ready for a new chat</div>
                <div class="chat-start-panel-grid">
                    <div class="chat-start-panel-item">
                        <span class="chat-start-panel-label">Model</span>
                        <span class="chat-start-panel-value">${utils.escapeHtml(modelText)}</span>
                    </div>
                    <div class="chat-start-panel-item">
                        <span class="chat-start-panel-label">Thinking</span>
                        <span class="chat-start-panel-value">${utils.escapeHtml(thinkingText)}</span>
                    </div>
                    <button type="button" class="chat-start-settings-button" data-chat-start-settings="true" aria-label="Change chat settings" title="Change chat settings">
                        <span>Change settings</span>
                        ${icons.SETTINGS_ICON_SVG}
                    </button>
                </div>
                ${renderWorkspaceRow(workspacePath)}
            `;
            attachChatStartPanelEvents(panel);
            container.appendChild(panel);
        }

        function selectedOptionText(select) {
            if (!(select instanceof HTMLSelectElement)) return '';
            const option = select.selectedOptions && select.selectedOptions.length
                ? select.selectedOptions[0]
                : null;
            return (option?.textContent || select.value || '').trim();
        }

        function renderWorkspaceRow(workspacePath) {
            return `
                <div class="chat-start-workspace-block">
                    <div class="chat-start-workspace-editor">
                        <span class="chat-start-workspace-label">Workspace</span>
                        ${workspacePath
                            ? `<span class="chat-start-workspace-path">${utils.escapeHtml(workspacePath)}</span>`
                            : renderWorkspaceEntryControls()}
                    </div>
                    <p class="chat-start-workspace-help">A workspace is a folder in your vault. Setting this helps orient the chat agent. See <a href="https://github.com/DodgyBadger/Assistant.md/blob/main/docs/use/getting-the-most.md" target="_blank" rel="noopener noreferrer">Getting the Most from Assistant.md</a> for more info.</p>
                </div>
            `;
        }

        function renderWorkspaceEntryControls() {
            if (!workspaceEditorOpen) {
                return '<button type="button" class="chat-start-link-button" data-chat-start-workspace-open="true">Add workspace</button>';
            }
            return `
                <input
                    type="text"
                    class="chat-start-workspace-input"
                    placeholder="Workspace path..."
                    aria-label="Workspace path"
                    data-chat-start-workspace-input
                />
                <button type="button" class="chat-start-icon-button" data-chat-start-workspace-browse="true" aria-label="Choose workspace folder" title="Choose workspace folder">
                    ${icons.FOLDER_ICON_SVG}
                </button>
                <button type="button" class="chat-start-link-button" data-chat-start-workspace-apply="true">Apply</button>
                <button type="button" class="chat-start-link-button is-muted" data-chat-start-workspace-cancel="true">Cancel</button>
            `;
        }

        function attachChatStartPanelEvents(panel) {
            panel.addEventListener('click', (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (target.closest('[data-chat-start-settings]')) {
                    callbacks.openChatSettings?.();
                    return;
                }
                if (target.closest('[data-chat-start-workspace-open]')) {
                    workspaceEditorOpen = true;
                    renderChatEmptyState();
                    return;
                }
                if (target.closest('[data-chat-start-workspace-cancel]')) {
                    workspaceEditorOpen = false;
                    renderChatEmptyState();
                    return;
                }
                if (target.closest('[data-chat-start-workspace-browse]')) {
                    callbacks.openWorkspacePicker?.();
                    return;
                }
                if (target.closest('[data-chat-start-workspace-apply]')) {
                    applyWorkspaceFromStartPanel(panel);
                }
            });
            panel.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter') return;
                const target = event.target;
                if (!(target instanceof HTMLInputElement) || !target.matches('[data-chat-start-workspace-input]')) {
                    return;
                }
                event.preventDefault();
                applyWorkspaceFromStartPanel(panel);
            });
        }

        function applyWorkspaceFromStartPanel(panel) {
            const input = panel.querySelector('[data-chat-start-workspace-input]');
            if (!(input instanceof HTMLInputElement)) return;
            const path = input.value.trim();
            if (!path || !elements.workspacePathInput) return;
            elements.workspacePathInput.value = path;
            elements.workspacePathInput.dispatchEvent(new Event('input', { bubbles: true }));
            workspaceEditorOpen = false;
            renderChatEmptyState();
        }

        function addChatErrorMessage(errorText) {
            addMessage('error', `Error: ${errorText || 'Streaming failed'}`);
        }


        function renderPersistedSession(payload, options = {}) {
            toolDetails.close();
            persistedToolEntriesById.clear();
            elements.chatMessages.innerHTML = '';

            const messages = Array.isArray(payload?.messages) ? payload.messages : [];
            const toolCallsById = groupToolCallsById(payload?.tool_calls);
            const pendingToolCallIds = new Set();

            if (messages.length === 0) {
                renderChatEmptyState('Selected session has no persisted messages.');
                renderLatestFailureAction(payload?.latest_failure);
                return;
            }

            messages.forEach((message) => {
                const forkSequenceIndex = Number.isInteger(message.fork_sequence_index)
                    ? message.fork_sequence_index
                    : message.sequence_index;
                if (message.is_tool_message) {
                    collectToolIds(message.tool_call_ids, pendingToolCallIds);
                    collectToolIds(message.tool_return_ids, pendingToolCallIds);
                    if (message.role === 'assistant' && (message.content || message.thinking_content)) {
                        renderPersistedAssistantMessage(message.content || '', [], {
                            sequenceIndex: forkSequenceIndex,
                            thinkingText: message.thinking_content || ''
                        });
                    }
                    return;
                }

                if (isCompactionSummaryMessage(message)) {
                    pendingToolCallIds.clear();
                    renderPersistedAssistantMessage(message.content || '', [], {
                        sequenceIndex: forkSequenceIndex
                    });
                    return;
                }

                if (message.role === 'assistant') {
                    const assistantToolCalls = toolCallsForIds(toolCallsById, pendingToolCallIds);
                    pendingToolCallIds.clear();
                    renderPersistedAssistantMessage(message.content || '', assistantToolCalls, {
                        sequenceIndex: forkSequenceIndex,
                        thinkingText: message.thinking_content || ''
                    });
                    return;
                }

                pendingToolCallIds.clear();
                addMessage('user', message.content || '', {
                    sequenceIndex: forkSequenceIndex
                });
            });

            renderLatestFailureAction(payload?.latest_failure);
            const reopenEntry = persistedToolEntriesById.get(options.reopenToolCallId || '');
            if (reopenEntry) {
                toolDetails.open(reopenEntry);
            }
        }

        function renderLatestFailureAction(latestFailure) {
            if (!latestFailure || latestFailure.status !== 'failed') {
                return;
            }

            const row = document.createElement('div');
            row.className = 'flex justify-start';

            const panel = document.createElement('div');
            panel.className = 'max-w-[80%] px-4 py-3 rounded-lg message-bubble message-error shadow-sm';

            const title = document.createElement('div');
            title.className = 'text-sm font-medium';
            const failureCopy = latestFailureCopy(latestFailure);
            title.textContent = failureCopy.title;

            const detail = document.createElement('div');
            detail.className = 'mt-1 text-xs opacity-80';
            detail.textContent = failureCopy.detail;

            panel.appendChild(title);
            panel.appendChild(detail);

            if (latestFailure.retryable && callbacks.retryLatestFailure) {
                const actions = document.createElement('div');
                actions.className = 'mt-3 flex items-center gap-2';
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'ui-icon-button is-compact';
                button.dataset.icon = 'refresh';
                button.dataset.iconLabel = 'Retry interrupted turn';
                button.title = 'Retry interrupted turn';
                button.setAttribute('aria-label', 'Retry interrupted turn');
                button.addEventListener('click', () => {
                    callbacks.retryLatestFailure(button);
                });
                actions.appendChild(button);
                panel.appendChild(actions);
            }

            row.appendChild(panel);
            appendChatMessageNode(row, { forceScroll: false });
            icons.hydrateIconButtons(row);
        }

        function latestFailureCopy(latestFailure) {
            if (latestFailure.failure_kind === 'provider_overloaded') {
                return {
                    title: 'The model service is temporarily overloaded.',
                    detail: 'The provider could not complete this response. You can retry the interrupted turn.'
                };
            }
            if (latestFailure.failure_kind === 'provider_unavailable') {
                return {
                    title: 'The model service is temporarily unavailable.',
                    detail: 'The provider could not complete this response. You can retry the interrupted turn.'
                };
            }
            if (latestFailure.failure_kind === 'rate_limited') {
                return {
                    title: 'The model service is temporarily rate-limited.',
                    detail: 'Wait briefly, then retry the interrupted turn.'
                };
            }
            if (latestFailure.failure_kind === 'model_stream_idle_timeout') {
                return {
                    title: 'The model stopped responding before completing this turn.',
                    detail: 'You can retry or switch models or providers if this keeps happening.'
                };
            }
            if (['transient_network', 'transient_provider'].includes(latestFailure.failure_kind)) {
                return {
                    title: 'The connection to the model service was interrupted.',
                    detail: 'You can retry the interrupted turn.'
                };
            }
            return {
                title: latestFailure.retryable
                    ? 'Response interrupted before it finished.'
                    : 'Response failed before it finished.',
                detail: latestFailure.error_type || latestFailure.failure_kind || 'Chat task failed'
            };
        }

        function groupToolCallsById(toolCalls) {
            const grouped = new Map();
            if (!Array.isArray(toolCalls)) {
                return grouped;
            }
            toolCalls.forEach((toolCall) => {
                if (!toolCall || !toolCall.tool_call_id) {
                    return;
                }
                grouped.set(toolCall.tool_call_id, toolCall);
            });
            return grouped;
        }

        function isCompactionSummaryMessage(message) {
            return String(message?.content || '').includes('AssistantMD compacted chat history');
        }

        function collectToolIds(toolIds, target) {
            if (!Array.isArray(toolIds) && !(toolIds instanceof Set)) {
                return;
            }
            toolIds.forEach((toolId) => {
                if (toolId) {
                    target.add(toolId);
                }
            });
        }

        function toolCallsForIds(toolCallsById, toolCallIds) {
            const selected = [];
            toolCallIds.forEach((toolId) => {
                const toolCall = toolCallsById.get(toolId);
                if (toolCall) {
                    selected.push(toolCall);
                }
            });
            return selected;
        }

        function renderPersistedAssistantMessage(content, toolCalls, options = {}) {
            const context = createAssistantStreamingMessage();
            context.fullText = content || '';
            context.thinkingText = options.thinkingText || '';
            context.collapseThinking = Boolean(context.thinkingText);
            context.thinkingExpanded = false;
            context.sequenceIndex = Number.isInteger(options.sequenceIndex) ? options.sequenceIndex : null;
            renderAssistantMarkdown(context, { finalize: true });
            hydratePersistedToolCalls(context, toolCalls);
            finalizeAssistantMessage(context, {
                sessionId: state.sessionId || 'unknown',
                messageCount: 1,
                toolCount: Array.isArray(toolCalls) ? toolCalls.length : 0,
                status: 'done'
            });
        }

        function hydratePersistedToolCalls(context, toolCalls) {
            if (!context || !Array.isArray(toolCalls) || toolCalls.length === 0) {
                return;
            }

            toolCalls.forEach((toolCall) => {
                if (!toolCall || !toolCall.tool_call_id) {
                    return;
                }

                let entry = context.toolStatusMap.get(toolCall.tool_call_id);
                if (!entry) {
                    ensureToolCallsSection(context);
                    entry = createToolStatusEntry(context, toolCall.tool_call_id, {
                        tool_name: toolCall.tool_name
                    });
                }
                entry.persisted = true;
                persistedToolEntriesById.set(toolCall.tool_call_id, entry);
                toolDetails.setEntryTokenCount(entry, toolCall.token_count);
                setToolEntryState(entry, toolCall.status || 'interrupted');
            });

            context.toolStatusMap.forEach((entry) => {
                if (entry.state === 'running') {
                    setToolEntryState(entry, 'interrupted');
                }
            });

            updateToolCallsSummary(context);
        }


        // Loading indicator helpers
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

            appendChatMessageNode(messageDiv, { forceScroll: true });

            return messageDiv;
        }

        function removeLoadingMessage(messageDiv) {
            if (messageDiv && messageDiv.parentNode) {
                messageDiv.parentNode.removeChild(messageDiv);
            }
        }


        // Add message to chat with copy controls
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

            const copyButton = createCopyButton(() => utils.getCopyableText(bodyDiv), 'message-copy-button');
            actionsDiv.appendChild(copyButton);
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

            appendChatMessageNode(messageDiv, { forceScroll: true });
        }

        function createAssistantStreamingMessage() {
            const messageDiv = document.createElement('div');
            messageDiv.className = 'flex justify-start';

            const contentDiv = document.createElement('div');
            contentDiv.className = 'max-w-[80%] px-4 py-3 rounded-lg message-bubble message-assistant prose prose-sm max-w-none shadow-sm';

            const progressDiv = document.createElement('div');
            progressDiv.className = 'stream-progress';

            const indicator = document.createElement('div');
            indicator.className = 'stream-status-indicator typing';
            indicator.innerHTML = icons.TYPING_DOTS_HTML;

            const statusText = document.createElement('span');
            statusText.className = 'stream-status-text';
            statusText.textContent = 'Assistant is responding';

            progressDiv.appendChild(indicator);
            progressDiv.appendChild(statusText);

            const toolList = document.createElement('div');
            toolList.className = 'tool-status-list hidden';

            const bodyDiv = document.createElement('div');
            bodyDiv.className = 'message-body prose prose-sm max-w-none';
            bodyDiv.innerHTML = '';

            const artifactList = document.createElement('div');
            artifactList.className = 'message-artifact-list';

            contentDiv.appendChild(progressDiv);
            contentDiv.appendChild(bodyDiv);
            contentDiv.appendChild(artifactList);
            messageDiv.appendChild(contentDiv);

            appendChatMessageNode(messageDiv, { forceScroll: true });

            return {
                messageDiv,
                contentDiv,
                progressDiv,
                indicator,
                statusText,
                bodyDiv,
                artifactList,
                thinkingDiv: null,
                thinkingTextSpan: null,
                thinkingToggle: null,
                toolList,
                toolCallsSection: null,
                toolCallsSummaryTitle: null,
                toolStatusMap: new Map(),
                fullText: '',
                thinkingText: '',
                collapseThinking: false,
                thinkingExpanded: false,
                errorMessages: [],
                toolSummary: null,
                postProcessTimer: null,
                toolElapsedTimer: null
            };
        }

        function ensureToolCallsSection(context) {
            if (context.toolCallsSection) {
                return context.toolCallsSection;
            }

            const section = document.createElement('details');
            section.className = 'tool-calls-section';

            const summary = document.createElement('summary');
            summary.className = 'tool-status-summary';

            const chevron = document.createElement('span');
            chevron.className = 'tool-status-chevron';
            chevron.textContent = '▸';

            const title = document.createElement('span');
            title.className = 'tool-status-title';
            title.textContent = 'Tool calls (0)';

            summary.appendChild(chevron);
            summary.appendChild(title);

            section.appendChild(summary);
            section.appendChild(context.toolList);
            context.contentDiv.appendChild(section);
            section.addEventListener('toggle', () => {
                chevron.textContent = section.open ? '▾' : '▸';
            });

            context.toolCallsSection = section;
            context.toolCallsSummaryTitle = title;
            return section;
        }

        function updateToolCallsSummary(context) {
            if (!context || !context.toolCallsSummaryTitle) {
                return;
            }

            const total = context.toolStatusMap.size;
            context.toolCallsSummaryTitle.textContent = `Tool calls (${total})`;
        }

        function appendAssistantDelta(context, delta, options = {}) {
            if (!context || !delta) {
                return;
            }
            const { render = true } = options;
            const answerStarted = !context.fullText;
            context.fullText += delta;
            if (answerStarted && context.thinkingText) {
                context.collapseThinking = true;
                context.thinkingExpanded = false;
            }
            if (render) {
                renderAssistantMarkdown(context);
            }
        }

        function appendAssistantThinkingDelta(context, delta, options = {}) {
            if (!context || !delta) {
                return;
            }
            const { render = true } = options;
            context.thinkingText += delta;
            if (context.fullText && !context.collapseThinking) {
                context.collapseThinking = true;
                context.thinkingExpanded = false;
            }
            if (render) {
                renderAssistantMarkdown(context);
            }
        }

        function resetAssistantStream(context, options = {}) {
            if (!context) {
                return;
            }
            const { render = true, showReconnectStatus = true } = options;
            context.fullText = '';
            context.thinkingText = '';
            context.collapseThinking = false;
            context.thinkingExpanded = false;
            context.errorMessages = [];
            if (render) {
                renderAssistantMarkdown(context);
            }
            if (showReconnectStatus) {
                setAssistantStatus(context, 'Reconnecting to model', 'thinking');
            }
        }

        function renderAssistantMarkdown(context, options = {}) {
            const { finalize = false } = options;
            renderAssistantThinking(context);
            markdown.renderHtml(context.bodyDiv, context.fullText);
            if (finalize) {
                markdown.flushPostProcess(context);
            } else {
                markdown.schedulePostProcess(context);
            }
            callbacks.scrollChatToBottom();
        }

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

        function setAssistantStatus(context, label, state = 'thinking') {
            context.statusText.textContent = label;
            context.indicator.className = 'stream-status-indicator';
            if (state === 'thinking') {
                context.indicator.classList.add('typing');
                context.indicator.innerHTML = icons.TYPING_DOTS_HTML;
                return;
            }
            context.indicator.classList.add('hidden');
            context.indicator.innerHTML = '';
        }

        function formatToolElapsed(entry) {
            const end = entry.finishedAt || Date.now();
            const elapsedSeconds = Math.max(0, Math.floor((end - entry.startedAt) / 1000));
            if (elapsedSeconds < 60) return `${elapsedSeconds}s`;
            const minutes = Math.floor(elapsedSeconds / 60);
            return `${minutes}m ${elapsedSeconds % 60}s`;
        }

        function setToolEntryState(entry, nextState) {
            if (!entry) return;
            entry.state = nextState;
            if (nextState === 'running') {
                entry.finishedAt = null;
            } else if (!entry.finishedAt) {
                entry.finishedAt = Date.now();
            }
            entry.container.classList.remove(
                'tool-status-running',
                'tool-status-complete',
                'tool-status-failed',
                'tool-status-interrupted'
            );
            const className = nextState === 'completed' ? 'tool-status-complete' : `tool-status-${nextState}`;
            entry.container.classList.add(className);
            const symbols = { running: '', completed: '✓', failed: '×', interrupted: '!' };
            entry.stateIcon.textContent = symbols[nextState] || '';
            updateToolElapsed(entry);
            if (toolDetails.getActiveEntry() === entry) {
                toolDetails.refresh(entry);
            }
        }

        function updateToolElapsed(entry) {
            if (!entry) return;
            const tokenLabel = entry.tokenCount === null
                ? ''
                : `, ${entry.tokenCount.toLocaleString()} ${entry.tokenCount === 1 ? 'token' : 'tokens'}`;
            entry.container.setAttribute(
                'aria-label',
                `${entry.toolName}: ${toolStateLabel(entry)}${tokenLabel}`
            );
            if (toolDetails.getActiveEntry() === entry) {
                const elapsedBlock = document.querySelector(
                    '#chat-tool-call-modal [data-tool-call-elapsed] .tool-status-block'
                );
                if (elapsedBlock) elapsedBlock.textContent = formatToolElapsed(entry);
            }
        }

        function toolStateLabel(entry) {
            const labels = {
                running: 'Running',
                completed: 'Complete',
                failed: 'Failed',
                interrupted: 'Interrupted'
            };
            return labels[entry.state] || entry.state;
        }

        function toolResultState(payload) {
            const outcome = String(payload?.outcome || '').trim().toLowerCase();
            if (outcome === 'interrupted' || payload?.terminal_state === 'interrupted') return 'interrupted';
            const metadata = payload?.result_metadata || payload?.metadata || {};
            const status = String(metadata.status || metadata.state || '').trim().toLowerCase();
            if (['failed', 'denied'].includes(outcome) || ['error', 'failed', 'failure'].includes(status)) {
                return 'failed';
            }
            return payload?.terminal_state === 'failed' ? 'failed' : 'completed';
        }

        function startToolElapsedTimer(context) {
            if (context.toolElapsedTimer) return;
            context.toolElapsedTimer = window.setInterval(() => {
                let hasRunning = false;
                context.toolStatusMap.forEach((entry) => {
                    if (entry.state !== 'running') return;
                    hasRunning = true;
                    updateToolElapsed(entry);
                });
                if (!hasRunning) stopToolElapsedTimer(context);
            }, 1000);
        }

        function stopToolElapsedTimer(context) {
            if (!context.toolElapsedTimer) return;
            window.clearInterval(context.toolElapsedTimer);
            context.toolElapsedTimer = null;
        }

        function handleToolEvent(context, payload) {
            const toolId = payload.tool_call_id || `tool-${context.toolStatusMap.size + 1}`;
            if (!toolId) return;

            let entry = context.toolStatusMap.get(toolId);

            if (payload.event === 'tool_call_started' || !entry) {
                ensureToolCallsSection(context);
                entry = createToolStatusEntry(context, toolId, payload);
                if (payload.event === 'tool_call_started') {
                    setAssistantStatus(context, 'Running tools', 'tools');
                }
            }

            if (payload.event === 'tool_call_finished') {
                toolDetails.setEntryTokenCount(entry, payload.token_count);
                setToolEntryState(entry, toolResultState(payload));
                if (toolDetails.getActiveEntry() === entry) {
                    if (entry.persisted) {
                        void toolDetails.load(entry, { force: true });
                    } else {
                        toolDetails.refresh(entry);
                    }
                }

                const hasRunning = Array.from(context.toolStatusMap.values())
                    .some(item => item.state === 'running');
                if (!hasRunning) {
                    stopToolElapsedTimer(context);
                    setAssistantStatus(context, 'Continuing response', 'thinking');
                }
            } else if (payload.event === 'tool_call_started') {
                setToolEntryState(entry, 'running');
                toolDetails.updateEntry(entry);
                startToolElapsedTimer(context);
            }
            updateToolCallsSummary(context);
        }

        function createToolStatusEntry(context, toolId, payload) {
            const container = document.createElement('button');
            container.type = 'button';
            container.className = 'tool-status tool-status-running';

            const summary = document.createElement('div');
            summary.className = 'tool-status-summary';

            const line = document.createElement('span');
            line.className = 'tool-status-line';

            const stateIcon = document.createElement('span');
            stateIcon.className = 'tool-status-state-icon';
            stateIcon.setAttribute('aria-hidden', 'true');

            summary.appendChild(stateIcon);
            summary.appendChild(line);

            container.appendChild(summary);
            container.addEventListener('click', () => {
                toolDetails.open(entry);
            });

            context.toolList.classList.remove('hidden');
            context.toolList.appendChild(container);
            const entry = {
                container,
                summary,
                line,
                stateIcon,
                toolId,
                toolName: payload.tool_name || 'Tool call',
                tokenCount: toolDetails.normalizeTokenCount(payload.token_count),
                persisted: false,
                detailUnavailable: false,
                detailArgs: null,
                detailResult: null,
                detailMetadata: {},
                detailArtifactRef: '',
                detailVault: '',
                detailSessionId: '',
                detailEvents: [],
                state: 'running',
                startedAt: Date.now(),
                finishedAt: null,
                detailLoaded: false,
                detailLoading: false,
                detailError: '',
                detailRequestId: 0,
                detailAbortController: null,
                modalAbortController: null
            };
            setToolEntryState(entry, 'running');
            toolDetails.updateEntry(entry);
            context.toolStatusMap.set(toolId, entry);

            return entry;
        }

        function finalizeAssistantMessage(context, metadata) {
            stopToolElapsedTimer(context);
            context.toolStatusMap.forEach((entry) => {
                if (entry.state === 'running') {
                    setToolEntryState(entry, 'interrupted');
                }
            });
            updateToolCallsSummary(context);
            renderAssistantMarkdown(context, { finalize: true });

            const hasError = context.errorMessages.length > 0;
            const endedEarly = metadata.status && metadata.status !== 'done' && !hasError;

            if (hasError || endedEarly) {
                const finalLabel = hasError ? 'Completed with issues' : 'Response ended early';
                setAssistantStatus(context, finalLabel, 'error');
            } else if (context.progressDiv && context.progressDiv.parentNode) {
                context.progressDiv.parentNode.removeChild(context.progressDiv);
            }

            const footerDiv = document.createElement('div');
            footerDiv.className = 'message-footer';

            const footerContent = document.createElement('div');
            footerContent.className = 'message-footer-content';
            const hasToolSection = Boolean(context.toolCallsSection && context.toolStatusMap.size > 0);

            if (hasToolSection && context.toolCallsSection) {
                footerDiv.classList.add('message-footer-has-tools');
                footerContent.appendChild(context.toolCallsSection);
            }

            if (endedEarly && !hasError) {
                const span = document.createElement('span');
                span.textContent = 'Status: Partial output';
                footerContent.appendChild(span);
            } else if (hasError) {
                const span = document.createElement('span');
                span.textContent = 'Status: Needs review';
                footerContent.appendChild(span);
            }

            const actionsDiv = document.createElement('div');
            actionsDiv.className = 'message-footer-actions';

            const copyButton = createCopyButton(() => utils.getCopyableText(context.bodyDiv), 'message-copy-button');
            actionsDiv.appendChild(copyButton);
            const forkButton = createForkButton(context.sequenceIndex);
            if (forkButton) {
                actionsDiv.appendChild(forkButton);
            }

            if (footerContent.childElementCount > 0) {
                footerDiv.appendChild(footerContent);
            } else {
                footerDiv.classList.add('message-footer-right');
            }
            footerDiv.appendChild(actionsDiv);
            context.contentDiv.appendChild(footerDiv);

            callbacks.scrollChatToBottom();
        }

        function reconcileToolCallPersistence(context, toolCalls) {
            if (!context?.toolStatusMap) return;
            const committedById = new Map();
            (Array.isArray(toolCalls) ? toolCalls : []).forEach((toolCall) => {
                if (toolCall?.tool_call_id) {
                    committedById.set(toolCall.tool_call_id, toolCall);
                }
            });
            context.toolStatusMap.forEach((entry) => {
                const committed = committedById.get(entry.toolId);
                entry.persisted = Boolean(committed);
                entry.detailUnavailable = !committed;
                toolDetails.setEntryTokenCount(entry, committed?.token_count);
                if (committed?.status) {
                    setToolEntryState(entry, committed.status);
                }
            });
            const activeEntry = toolDetails.getActiveEntry();
            if (
                activeEntry
                && context.toolStatusMap.get(activeEntry.toolId) === activeEntry
            ) {
                toolDetails.refresh(activeEntry);
                if (activeEntry.persisted) {
                    void toolDetails.load(activeEntry);
                }
            }
        }

        function attachCodeCopyButtons(container) {
            const codeBlocks = container.querySelectorAll('pre');
            codeBlocks.forEach(pre => {
                if (pre.querySelector('.code-copy-button')) return;
                const copyButton = createCopyButton(() => utils.getCopyableText(pre), 'code-copy-button');
                pre.appendChild(copyButton);
            });
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
                addChatErrorMessage(`Fork failed: ${error.message}`);
                button.disabled = previousDisabled;
            }
        }

        return Object.freeze({
            renderEmptyState: renderChatEmptyState,
            refreshEmptyState,
            addErrorMessage: addChatErrorMessage,
            renderPersistedSession,
            addMessage,
            addLoadingMessage,
            removeLoadingMessage,
            createAssistantStreamingMessage,
            appendAssistantDelta,
            appendAssistantThinkingDelta,
            resetAssistantStream,
            renderAssistantMarkdown,
            setAssistantStatus,
            handleToolEvent,
            finalizeAssistantMessage,
            reconcileToolCallPersistence,
            renderMarkdownPreview: markdown.renderPreview,
            closeToolCallDetails: toolDetails.close,
            getActiveToolDetailId: toolDetails.getActiveId,
        });
    }

    window.ChatRendering = Object.freeze({
        create: createChatRenderingController,
    });
})(window, document);
