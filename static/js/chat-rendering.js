(function chatRenderingModule(window, document) {
    const CHAT_EMPTY_STATE_MESSAGE = 'Start a conversation...';

    function createChatRenderingController({ state, elements, icons, utils, callbacks }) {
        let currentEmptyStateMessage = CHAT_EMPTY_STATE_MESSAGE;
        const markdown = window.ChatMarkdown.create({
            utils,
            callbacks: {
                attachCodeCopyButtons,
                enhanceFileLinks: callbacks.enhanceFileLinks,
                scrollChatToBottom: callbacks.scrollChatToBottom,
            },
        });
        const messageControls = window.ChatMessageControls.create({
            state,
            elements,
            icons,
            utils,
            markdown,
            callbacks: {
                appendMessageNode: appendChatMessageNode,
                addErrorMessage: addChatErrorMessage,
                fetchSessions: callbacks.fetchSessions,
                loadSession: callbacks.loadSession,
            },
        });
        const toolDetails = window.ChatToolDetails.create({
            state,
            elements,
            icons,
            utils,
            callbacks: {
                createCopyButton: messageControls.createCopyButton,
                formatToolElapsed,
                renderEditProposalArtifact: callbacks.renderEditProposalArtifact,
                toolStateLabel,
            },
        });
        const startPanel = window.ChatStartPanel.create({
            elements,
            icons,
            utils,
            callbacks: {
                openChatSettings: callbacks.openChatSettings,
                openWorkspacePicker: callbacks.openWorkspacePicker,
                renderEmptyState: () => renderChatEmptyState(),
            },
        });
        const thinking = window.ChatThinking.create();
        const historyRendering = window.ChatHistoryRendering.create({
            state,
            elements,
            icons,
            toolDetails,
            messageControls,
            callbacks: {
                renderEmptyState: renderChatEmptyState,
                appendMessageNode: appendChatMessageNode,
                createAssistantStreamingMessage,
                renderAssistantMarkdown,
                ensureToolCallsSection,
                createToolStatusEntry,
                setToolEntryState,
                updateToolCallsSummary,
                finalizeAssistantMessage,
                retryLatestFailure: callbacks.retryLatestFailure,
            },
        });

        function renderChatStartPanel(container) {
            startPanel.render(container);
        }

        function renderAssistantThinking(context) {
            thinking.render(context);
        }

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
            historyRendering.clear();
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

        function addChatErrorMessage(errorText) {
            messageControls.addMessage('error', `Error: ${errorText || 'Streaming failed'}`);
        }




        // Loading indicator helpers

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

            const copyButton = messageControls.createCopyButton(() => utils.getCopyableText(context.bodyDiv), 'message-copy-button');
            actionsDiv.appendChild(copyButton);
            const forkButton = messageControls.createForkButton(context.sequenceIndex);
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
                const copyButton = messageControls.createCopyButton(() => utils.getCopyableText(pre), 'code-copy-button');
                pre.appendChild(copyButton);
            });
        }


        return Object.freeze({
            renderEmptyState: renderChatEmptyState,
            refreshEmptyState,
            addErrorMessage: addChatErrorMessage,
            renderPersistedSession: historyRendering.renderSession,
            addMessage: messageControls.addMessage,
            addLoadingMessage: messageControls.addLoadingMessage,
            removeLoadingMessage: messageControls.removeLoadingMessage,
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
