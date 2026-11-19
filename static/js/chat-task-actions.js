/** Chat task submission, continuation, retry, and cancellation actions. */
(function chatTaskActionsModule(window) {
    function createChatTaskActionsController({
        state,
        composeState,
        elements,
        icons,
        chatRendering,
        chatTaskStream,
        sessionControls,
        workspacePicker,
        callbacks,
    }) {
        let postTurnRefreshController = null;
        const streamSettlements = new WeakMap();

        async function refreshSessionAfterTask(taskId, vault, sessionId, streamController) {
            postTurnRefreshController?.abort();
            const controller = new AbortController();
            postTurnRefreshController = controller;
            const isCurrent = () => postTurnRefreshController === controller
                && state.sessionId === sessionId && elements.vaultSelector?.value === vault
                && (!state.activeChatAbortController || state.activeChatAbortController === streamController);
            // This bounds a UI observer only; it never cancels the execution task.
            const deadline = window.setTimeout(() => {
                console.warn('Timed out waiting to refresh compaction state after chat completion.', { taskId, sessionId });
                controller.abort();
            }, 5 * 60 * 1000);
            try {
                const completed = await chatTaskStream.waitForTerminal(taskId, controller.signal, { isCurrent });
                if (completed && isCurrent()) {
                    await sessionControls.refreshCompactionState({ signal: controller.signal, isCurrent });
                }
            } catch (error) {
                if (!controller.signal.aborted) {
                    console.warn('Could not refresh compaction state after chat completion.', error);
                }
            } finally {
                window.clearTimeout(deadline);
                if (postTurnRefreshController === controller) postTurnRefreshController = null;
            }
        }

        async function streamStartedChatTask(started, vault, abortController, options = {}) {
            const completion = consumeStartedChatTask(started, vault, abortController, options);
            streamSettlements.set(abortController, completion);
            try { return await completion; }
            finally { streamSettlements.delete(abortController); }
        }

        async function consumeStartedChatTask(
            started,
            vault,
            abortController,
            { hydrateReplay = false } = {}
        ) {
            postTurnRefreshController?.abort();
            if (started.session_id) {
                state.sessionId = started.session_id;
                callbacks.syncChatControlLocks();
                sessionControls.renderSelector();
                sessionControls.updateTitleRow();
                sessionControls.refreshCompactionState();
            }
            const taskId = started.task?.task_id;
            if (!taskId) {
                throw new Error('Chat task did not return a task id.');
            }
            state.activeChatTaskId = taskId;
            callbacks.syncChatControlLocks();
            const sessionId = state.sessionId;

            const assistantMessage = chatRendering.createAssistantStreamingMessage();
            let hydrated = null;
            if (hydrateReplay) {
                try {
                    hydrated = await chatTaskStream.hydrateReplaySnapshot(
                        taskId,
                        assistantMessage,
                        abortController
                    );
                } catch (error) {
                    if (abortController.signal.aborted || error.name === 'AbortError') throw error;
                    console.warn('Could not hydrate chat replay snapshot; continuing with live events.', error);
                    chatRendering.setAssistantStatus(assistantMessage, 'Reconnecting…', 'thinking');
                }
            }
            if (hydrated?.currentTaskId) {
                state.activeChatTaskId = hydrated.currentTaskId;
            }
            const streamResult = await chatTaskStream.consumeEvents(
                taskId,
                assistantMessage,
                abortController,
                hydrated || {}
            );

            const emptyDuplicateReviewMessage = (
                streamResult.finishReason === 'tool_review_required'
                && !assistantMessage.fullText
                && !assistantMessage.thinkingText
                && assistantMessage.toolStatusMap.size === 0
                && assistantMessage.artifactList.childElementCount === 0
            );
            if (emptyDuplicateReviewMessage) {
                assistantMessage.messageDiv.remove();
            } else {
                chatRendering.finalizeAssistantMessage(assistantMessage, {
                    sessionId: state.sessionId || 'unknown',
                    messageCount: Math.max(streamResult.messageCount, assistantMessage.fullText ? 1 : 0),
                    toolCount: assistantMessage.toolStatusMap.size,
                    status: streamResult.finished ? 'done' : 'incomplete'
                });
            }
            if (streamResult.finishReason === 'tool_review_required') {
                await callbacks.reconcileCommittedToolCalls(
                    assistantMessage,
                    vault,
                    state.sessionId || ''
                );
            }
            if (vault && streamResult.finishReason !== 'tool_review_required') {
                await callbacks.fetchSessions(vault, state.sessionId || '');
                if (state.sessionId) {
                    const reopenToolCallId = chatRendering.getActiveToolDetailId();
                    await callbacks.loadSession(state.sessionId, { reopenToolCallId });
                }
            }
            if (streamResult.finished && streamResult.finishReason === 'stop'
                && state.sessionId === sessionId && elements.vaultSelector?.value === vault
                && state.activeChatAbortController === abortController) {
                // Render the answer first. Its initial history load must not overwrite
                // the later checkpoint snapshot when post-turn compaction finishes.
                void refreshSessionAfterTask(state.activeChatTaskId || taskId, vault, sessionId, abortController);
            }
        }

        async function streamDeferredReviewTask(started, { hydrateReplay = false } = {}) {
            if (state.isLoading) {
                chatRendering.addErrorMessage('Review submitted, but the follow-up response could not start because chat is busy.');
                return false;
            }
            const vault = elements.vaultSelector?.value || '';
            if (!vault) {
                chatRendering.addErrorMessage('Review submitted, but the follow-up response could not start because no vault is selected.');
                return false;
            }
            state.isLoading = true;
            state.isCancellingChat = false;
            state.activeChatSessionId = started.session_id || state.sessionId || null;
            const abortController = new AbortController();
            state.activeChatAbortController = abortController;
            elements.sendBtn.disabled = true;
            callbacks.syncChatControlLocks();

            try {
                await streamStartedChatTask(started, vault, abortController, { hydrateReplay });
                return true;
            } catch (error) {
                console.error('Error streaming deferred review task:', error);
                if (state.isCancellingChat || error.name === 'AbortError') {
                    chatRendering.addMessage('assistant', 'Response stopped.');
                } else {
                    chatRendering.addErrorMessage(error.message);
                }
                return false;
            } finally {
                if (chatTaskStream.releaseActiveStream(abortController)) {
                    elements.sendBtn.disabled = false;
                    callbacks.syncChatControlLocks();
                    elements.chatInput.focus();
                    sessionControls.refreshCompactionState();
                }
            }
        }

        async function retryLatestFailure(button = null) {
            const vault = elements.vaultSelector?.value || '';
            const sessionId = state.sessionId;
            if (!vault || !sessionId || state.isLoading) return;

            state.isLoading = true;
            state.activeChatSessionId = sessionId;
            const abortController = new AbortController();
            state.activeChatAbortController = abortController;
            if (button) {
                button.disabled = true;
                icons.setIconButtonLabel(button, 'Retrying interrupted turn...');
            }
            callbacks.syncChatControlLocks();

            const loadingMessage = chatRendering.addLoadingMessage();
            try {
                const response = await fetch(`api/chat/sessions/${encodeURIComponent(sessionId)}/retry`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ vault_name: vault }),
                    signal: abortController.signal
                });
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.message || `HTTP ${response.status}`);
                }
                chatRendering.removeLoadingMessage(loadingMessage);
                const started = await response.json();
                await streamStartedChatTask(started, vault, abortController);
            } catch (error) {
                console.error('Error retrying chat turn:', error);
                chatRendering.removeLoadingMessage(loadingMessage);
                if (state.isCancellingChat || error.name === 'AbortError') {
                    chatRendering.addMessage('assistant', 'Response stopped.');
                } else {
                    chatRendering.addErrorMessage(error.message);
                }
            } finally {
                if (chatTaskStream.releaseActiveStream(abortController)) {
                    if (button) {
                        button.disabled = false;
                        icons.setIconButtonLabel(button, 'Retry interrupted turn');
                    }
                    callbacks.syncChatControlLocks();
                    elements.chatInput.focus();
                    sessionControls.refreshCompactionState();
                }
            }
        }

        function buildChatRequestData(prompt, sessionId) {
            if (state.sessionId !== sessionId) throw new Error('The chat has changed. Reopen its browser to continue.');
            const vault = elements.vaultSelector.value;
            const model = elements.modelSelector.value;
            if (!vault || !model) throw new Error('Select a vault and model before returning with instructions.');
            return {
                vault_name: vault, prompt, model,
                thinking: elements.thinkingSelector?.value || 'default',
                chat_mode: elements.chatModeSelector?.value || 'normal',
                context_template: elements.templateSelector?.value || null,
                workspace_path: workspacePicker.currentPath() || null,
                session_id: sessionId,
            };
        }

        async function followHandoffRevision(started, sessionId) {
            if (state.sessionId !== sessionId) return false;
            const previous = state.activeChatAbortController;
            previous?.abort();
            // Wait for the old stream's history reconciliation as well as SSE.
            // Its late refresh must not replace the new turn's message context.
            const settling = previous && streamSettlements.get(previous);
            if (settling) await settling.catch(() => {});
            if (previous) chatTaskStream.releaseActiveStream(previous);
            if (state.sessionId !== sessionId) return false;
            await callbacks.loadSession(sessionId, { skipActiveTaskCheck: true });
            if (state.sessionId !== sessionId) return false;
            // Follow the exact accepted replacement, including its replay.
            return streamDeferredReviewTask(started, { hydrateReplay: true });
        }

        // Send message handler with streaming response support
        async function sendMessage(promptOverride = null, {
            includeAttachments = true,
            focusComposerOnFinish = true,
            onAccepted = null,
            onStartError = null,
            onSessionAssigned = null,
        } = {}) {
            let accepted = false;
            const hasStringPromptOverride = typeof promptOverride === 'string' && promptOverride.trim();
            const hasPromptOverride = hasStringPromptOverride;
            const message = hasPromptOverride ? promptOverride.trim() : elements.chatInput.value.trim();
            const pendingUploads = includeAttachments ? composeState.pendingAttachments.slice() : [];
            if ((!message && pendingUploads.length === 0) || state.isLoading || state.pendingDeferredReview || state.chatModeSavePending) {
                onStartError?.(new Error('Wait for the active response, review, or settings save to finish.'));
                return false;
            }
            const effectivePrompt = message || 'Please analyze the attached image(s).';

            const vault = elements.vaultSelector.value;
            const model = elements.modelSelector.value;
            const thinking = elements.thinkingSelector ? (elements.thinkingSelector.value || 'default') : 'default';

            if (!vault) {
                if (onStartError) onStartError(new Error('Please select a vault.'));
                else alert('Please select a vault');
                return false;
            }

            if (!model) {
                if (onStartError) onStartError(new Error('Please select a model.'));
                else alert('Please select a model');
                return false;
            }

            const userMessageText = pendingUploads.length > 0
                ? `${effectivePrompt}\n\n[Attached images]\n${pendingUploads.map((item) => `- ${item.file.name}`).join('\n')}`
                : effectivePrompt;
            chatRendering.addMessage('user', userMessageText.trim());
            if (!hasPromptOverride) {
                elements.chatInput.value = '';
            }
            state.isLoading = true;
            elements.sendBtn.disabled = true;
            callbacks.syncChatControlLocks();

            const contextTemplateValue = elements.templateSelector ? elements.templateSelector.value || null : null;
            const chatModeValue = elements.chatModeSelector ? elements.chatModeSelector.value || 'normal' : 'normal';
            const workspacePathValue = workspacePicker.currentPath() || null;
            const requestSessionId = state.sessionId || callbacks.createClientSessionId(vault);
            state.sessionId = requestSessionId;
            onSessionAssigned?.(requestSessionId);
            sessionControls.renderSelector();
            sessionControls.updateTitleRow();
            sessionControls.refreshCompactionState();
            callbacks.syncChatControlLocks();
            state.activeChatSessionId = requestSessionId;
            const abortController = new AbortController();
            state.activeChatAbortController = abortController;

            const loadingMessage = chatRendering.addLoadingMessage();

            try {
                let startResponse;
                if (pendingUploads.length > 0) {
                    const formData = new FormData();
                    formData.append('vault_name', vault);
                    formData.append('prompt', effectivePrompt);
                    formData.append('model', model);
                    formData.append('thinking', thinking);
                    formData.append('chat_mode', chatModeValue);
                    if (contextTemplateValue) {
                        formData.append('context_template', contextTemplateValue);
                    }
                    if (workspacePathValue) {
                        formData.append('workspace_path', workspacePathValue);
                    }
                    formData.append('session_id', requestSessionId);
                    pendingUploads.forEach((item) => {
                        formData.append('images', item.file, item.file.name);
                    });
                    startResponse = await fetch('api/chat/tasks', {
                        method: 'POST',
                        body: formData,
                        signal: abortController.signal
                    });
                } else {
                    const requestData = buildChatRequestData(effectivePrompt, requestSessionId);
                    startResponse = await fetch('api/chat/tasks', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(requestData),
                        signal: abortController.signal
                    });
                }

                if (!startResponse.ok) {
                    const errorData = await startResponse.json().catch(() => ({}));
                    throw new Error(errorData.message || `HTTP ${startResponse.status}`);
                }

                const started = await startResponse.json();
                if (!started.task?.task_id) throw new Error('Chat task did not return a task id.');
                accepted = true;
                if (includeAttachments) callbacks.clearPendingAttachments();
                chatRendering.removeLoadingMessage(loadingMessage);
                onAccepted?.(started);
                await streamStartedChatTask(started, vault, abortController);
                return true;

            } catch (error) {
                if (!accepted) onStartError?.(error);
                console.error('Error sending message:', error);
                chatRendering.removeLoadingMessage(loadingMessage);
                if (state.isCancellingChat || error.name === 'AbortError') {
                    chatRendering.addMessage('assistant', 'Response stopped.');
                } else {
                    chatRendering.addErrorMessage(error.message);
                }
                return false;
            } finally {
                if (chatTaskStream.releaseActiveStream(abortController)) {
                    elements.sendBtn.disabled = false;
                    callbacks.syncChatControlLocks();
                    if (focusComposerOnFinish) elements.chatInput.focus();
                    sessionControls.refreshCompactionState();
                }
            }
        }

        async function stopChatResponse() {
            if (!state.isLoading || state.isCancellingChat) return;
            const taskId = state.activeChatTaskId;
            const sessionId = state.activeChatSessionId || state.sessionId;
            if (!taskId && !sessionId) return;

            state.isCancellingChat = true;
            callbacks.syncChatControlLocks();
            try {
                let response = null;
                if (taskId) {
                    response = await fetch(
                        `api/tasks/${encodeURIComponent(taskId)}/cancel`,
                        { method: 'POST' }
                    );
                }
                let taskCancellationWasStale = false;
                if (response?.ok) {
                    const cancellation = await response.json().catch(() => ({}));
                    taskCancellationWasStale = cancellation.cancelled === false;
                }
                if (
                    (!response || response.status === 404 || taskCancellationWasStale)
                    && sessionId
                ) {
                    response = await fetch(
                        `api/chat/sessions/${encodeURIComponent(sessionId)}/cancel`,
                        { method: 'POST' }
                    );
                }
                if (response.status === 404) {
                    if (state.activeChatAbortController) {
                        state.activeChatAbortController.abort();
                    }
                    return;
                }
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.message || `HTTP ${response.status}`);
                }
            } catch (error) {
                console.error('Error stopping chat response:', error);
                state.isCancellingChat = false;
                callbacks.syncChatControlLocks();
                chatRendering.addErrorMessage(`Failed to stop response: ${error.message}`);
            }
        }

        return Object.freeze({
            retryLatestFailure,
            sendMessage,
            stopChatResponse,
            streamDeferredReviewTask,
            streamStartedTask: streamStartedChatTask,
            buildChatRequestData,
            followHandoffRevision,
        });
    }

    window.ChatTaskActions = Object.freeze({
        create: createChatTaskActionsController,
    });
})(window);
