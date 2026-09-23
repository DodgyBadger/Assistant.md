(function chatHistoryRenderingModule(window, document) {
    function createChatHistoryRendering({ state, elements, icons, toolDetails, messageControls, callbacks }) {
        const persistedToolEntriesById = new Map();

        function renderPersistedSession(payload, options = {}) {
            toolDetails.close();
            persistedToolEntriesById.clear();
            elements.chatMessages.innerHTML = '';

            const messages = Array.isArray(payload?.messages) ? payload.messages : [];
            const toolCallsById = groupToolCallsById(payload?.tool_calls);
            const pendingToolCallIds = new Set();

            if (messages.length === 0) {
                callbacks.renderEmptyState('Selected session has no persisted messages.');
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
                messageControls.addMessage('user', message.content || '', {
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
            callbacks.appendMessageNode(row, { forceScroll: false });
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
            const context = callbacks.createAssistantStreamingMessage();
            context.fullText = content || '';
            context.thinkingText = options.thinkingText || '';
            context.collapseThinking = Boolean(context.thinkingText);
            context.thinkingExpanded = false;
            context.sequenceIndex = Number.isInteger(options.sequenceIndex) ? options.sequenceIndex : null;
            callbacks.renderAssistantMarkdown(context, { finalize: true });
            hydratePersistedToolCalls(context, toolCalls);
            callbacks.finalizeAssistantMessage(context, {
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
                    callbacks.ensureToolCallsSection(context);
                    entry = callbacks.createToolStatusEntry(context, toolCall.tool_call_id, {
                        tool_name: toolCall.tool_name
                    });
                }
                entry.persisted = true;
                persistedToolEntriesById.set(toolCall.tool_call_id, entry);
                toolDetails.setEntryTokenCount(entry, toolCall.token_count);
                callbacks.setToolEntryState(entry, toolCall.status || 'interrupted');
            });

            context.toolStatusMap.forEach((entry) => {
                if (entry.state === 'running') {
                    callbacks.setToolEntryState(entry, 'interrupted');
                }
            });

            callbacks.updateToolCallsSummary(context);
        }

        return Object.freeze({
            renderSession: renderPersistedSession,
            clear: () => persistedToolEntriesById.clear(),
        });
    }

    window.ChatHistoryRendering = Object.freeze({
        create: createChatHistoryRendering,
    });
})(window, document);
