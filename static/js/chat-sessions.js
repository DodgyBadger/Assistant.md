(function chatSessionsModule(window) {
    function createChatSessions({ state, elements, sessionControls, chatRendering, chatTaskActions, chatTaskStream, callbacks }) {
        let sessionsRequestId = 0;
        let sessionLoadRequestId = 0;

        async function fetchSessions(vault, preferredSessionId = '') {
            if (vault !== (elements.vaultSelector?.value || '')) return;
            const requestId = ++sessionsRequestId;
            const isCurrent = () => requestId === sessionsRequestId && elements.vaultSelector?.value === vault;
            const selectedSessionId = state.sessionId;
            state.sessions = [];
            sessionControls.renderSelector();
            if (!vault) {
                sessionControls.clearCompactionProgress();
                return;
            }
            try {
                const response = await fetch(`api/chat/sessions?vault_name=${encodeURIComponent(vault)}`);
                if (!response.ok) {
                    throw new Error('Failed to fetch chat sessions');
                }
                const sessions = await response.json();
                if (!isCurrent()) return;
                state.sessions = sessions;
                sessionControls.renderSelector();
                if (
                    preferredSessionId
                    && state.sessionId === selectedSessionId
                    && state.sessions.some((session) => session.session_id === preferredSessionId)
                ) {
                    state.sessionId = preferredSessionId;
                    sessionControls.renderSelector();
                }
                await sessionControls.refreshCompactionProgress();
            } catch (error) {
                if (isCurrent()) console.error('Error fetching chat sessions:', error);
            }
        }

        async function loadSession(sessionId, options = {}) {
            const requestId = ++sessionLoadRequestId;
            const vault = elements.vaultSelector?.value || '';
            if (!vault || !sessionId) return;
            const isCurrent = () => (
                requestId === sessionLoadRequestId
                && state.sessionId === sessionId
                && elements.vaultSelector?.value === vault
            );

            chatRendering.closeToolCallDetails();
            let loadedSessionId = '';
            let loadedHistoryRevision = null;
            try {
                state.pendingDeferredReview = null;
                state.sessionId = sessionId;
                sessionControls.renderSelector();
                void sessionControls.refreshCompactionProgress();
                state.isLoading = true;
                callbacks.syncControls();
                const response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(sessionId)}?vault_name=${encodeURIComponent(vault)}`
                );
                if (!response.ok) {
                    throw new Error('Failed to load chat session');
                }
                const payload = await response.json();
                if (!isCurrent()) return;
                if (payload.session_id && payload.session_id !== sessionId) {
                    throw new Error('Loaded chat session does not match the requested session');
                }

                state.sessionId = payload.session_id || sessionId;
                loadedSessionId = state.sessionId;
                loadedHistoryRevision = Number.isInteger(payload.history_revision)
                    ? payload.history_revision
                    : null;
                if (elements.chatModeSelector) {
                    elements.chatModeSelector.value = payload.chat_mode === 'inline_edit'
                        ? 'inline_edit'
                        : 'normal';
                }
                state.pendingDeferredReview = payload.pending_review || null;
                state.workspaceExists = payload.workspace ? payload.workspace.exists === true : null;
                if (elements.workspacePathInput) {
                    elements.workspacePathInput.value = payload.workspace?.path || '';
                }
                chatRendering.renderPersistedSession(payload, options);
                if (state.pendingDeferredReview) {
                    const reviewMessage = chatRendering.createAssistantStreamingMessage();
                    callbacks.handleDeferredReview(reviewMessage, state.pendingDeferredReview);
                    chatRendering.setAssistantStatus(reviewMessage, 'Waiting for review', 'tools');
                }
                sessionControls.renderSelector();
                callbacks.updateStatus();
            } catch (error) {
                if (!isCurrent()) return;
                console.error('Error loading chat session:', error);
                callbacks.addErrorMessage(error.message);
            } finally {
                if (isCurrent()) {
                    state.isLoading = false;
                    callbacks.syncControls();
                }
            }
            if (
                loadedSessionId
                && requestId === sessionLoadRequestId
                && !options.skipActiveTaskCheck
                && !state.activeChatTaskId
                && state.sessionId === loadedSessionId
                && elements.vaultSelector?.value === vault
            ) {
                await reattachActiveTask(loadedSessionId, vault, {
                    historyRevision: loadedHistoryRevision,
                    loadRequestId: requestId,
                });
            }
        }

        async function reconcileCommittedToolCalls(context, vault, sessionId) {
            try {
                const response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(sessionId)}?vault_name=${encodeURIComponent(vault)}`,
                    { cache: 'no-store' }
                );
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const payload = await response.json();
                if (state.sessionId !== sessionId || elements.vaultSelector?.value !== vault) return;
                chatRendering.reconcileToolCallPersistence(context, payload.tool_calls);
            } catch (error) {
                console.warn('Unable to reconcile committed tool details:', error);
                if (state.sessionId === sessionId && elements.vaultSelector?.value === vault) {
                    chatRendering.reconcileToolCallPersistence(context, []);
                }
            }
        }

        async function reattachActiveTask(
            sessionId,
            vault,
            { historyRevision = null, loadRequestId = sessionLoadRequestId } = {}
        ) {
            const isCurrent = () => (
                loadRequestId === sessionLoadRequestId
                && state.sessionId === sessionId
                && elements.vaultSelector?.value === vault
            );
            let response;
            try {
                response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(sessionId)}/active-task`,
                    { cache: 'no-store' }
                );
            } catch (error) {
                if (isCurrent()) console.warn('Could not check for an active chat task:', error);
                return;
            }
            if (!isCurrent()) return;
            if (response.status === 404) {
                const errorData = await response.json().catch(() => ({}));
                if (!isCurrent()) return;
                const currentRevision = errorData?.details?.history_revision;
                if (
                    Number.isInteger(historyRevision)
                    && Number.isInteger(currentRevision)
                    && currentRevision !== historyRevision
                ) {
                    await loadSession(sessionId, { skipActiveTaskCheck: true });
                }
                return;
            }
            if (!response.ok) {
                console.warn(`Could not check for an active chat task: HTTP ${response.status}`);
                return;
            }

            const task = await response.json();
            if (!isCurrent()) return;
            if (!task?.task_id || !['queued', 'running'].includes(task.status)) return;
            if (state.activeChatTaskId) return;

            const abortController = new AbortController();
            state.isLoading = true;
            state.isCancellingChat = false;
            state.activeChatSessionId = sessionId;
            state.activeChatTaskId = task.task_id;
            state.activeChatAbortController = abortController;
            callbacks.syncControls();
            try {
                await chatTaskActions.streamStartedTask(
                    { session_id: sessionId, task },
                    vault,
                    abortController,
                    { hydrateReplay: true }
                );
            } catch (error) {
                if (isCurrent()) console.error('Error reattaching to active chat task:', error);
                if (isCurrent() && !state.isCancellingChat && error.name !== 'AbortError') {
                    callbacks.addErrorMessage(error.message);
                }
            } finally {
                if (chatTaskStream.releaseActiveStream(abortController)) {
                    callbacks.syncControls();
                }
            }
        }

        return Object.freeze({
            fetchSessions,
            loadSession,
            reconcileCommittedToolCalls,
            reattachActiveTask,
        });
    }

    window.ChatSessions = Object.freeze({
        create: createChatSessions,
    });
})(window);
