/** Chat task SSE consumption, replay hydration, and reconnect lifecycle. */
(function chatTaskStreamModule(window) {
    const CHAT_STREAM_MAX_RECONNECTS = 3;
    const CHAT_STREAM_RECONNECT_BASE_DELAY_MS = 500;

    function createChatTaskStreamController({ state, chatRendering, callbacks }) {
        function parseSseEvent(rawEvent) {
            if (!rawEvent) return null;

            const lines = rawEvent.split('\n');
            const dataLines = [];

            for (const line of lines) {
                const trimmed = line.trim();
                if (trimmed.startsWith('data:')) {
                    dataLines.push(trimmed.slice(5).trim());
                }
            }

            if (!dataLines.length) {
                try {
                    return JSON.parse(rawEvent);
                } catch {
                    return null;
                }
            }

            const dataPayload = dataLines.join('\n');
            if (!dataPayload) return null;

            try {
                return JSON.parse(dataPayload);
            } catch (error) {
                console.warn('Failed to parse SSE chunk:', dataPayload, error);
                return null;
            }
        }

        function applyChatStreamPayload(payload, assistantMessage, options = {}) {
            const { render = true } = options;
            const eventType = payload.event || 'delta';
            if (eventType === 'delta') {
                const delta = payload.choices?.[0]?.delta?.content;
                if (delta) {
                    chatRendering.appendAssistantDelta(assistantMessage, delta, { render });
                    chatRendering.setAssistantStatus(assistantMessage, 'Assistant is responding', 'thinking');
                }
                return { finished: false, messageCount: 0 };
            }

            if (eventType === 'thinking_delta') {
                const delta = payload.delta?.content;
                if (delta) {
                    chatRendering.appendAssistantThinkingDelta(assistantMessage, delta, { render });
                    chatRendering.setAssistantStatus(assistantMessage, 'Assistant is responding', 'thinking');
                }
                return { finished: false, messageCount: 0 };
            }

            if (eventType === 'chat_retry_scheduled') {
                if (payload.reset_response) {
                    chatRendering.resetAssistantStream(assistantMessage, { render });
                }
                return { finished: false, messageCount: 0 };
            }

            if (eventType === 'chat_retry_redirect') {
                if (payload.reset_response) {
                    chatRendering.resetAssistantStream(assistantMessage, { render });
                }
                return {
                    finished: false,
                    messageCount: 0,
                    nextTaskId: payload.replacement_task_id || '',
                };
            }

            if (eventType === 'chat_event_cursor_expired') {
                chatRendering.resetAssistantStream(assistantMessage);
                chatRendering.setAssistantStatus(assistantMessage, 'Finishing in background…', 'thinking');
                return { finished: false, messageCount: 0, eventGap: true };
            }

            if (eventType === 'tool_call_started' || eventType === 'tool_call_finished') {
                chatRendering.handleToolEvent(assistantMessage, payload);
                return { finished: false, messageCount: 0 };
            }

            if (eventType === 'review_required') {
                callbacks.handleDeferredReviewEvent(assistantMessage, payload);
                chatRendering.setAssistantStatus(assistantMessage, 'Waiting for review', 'tools');
                return { finished: false, messageCount: 0 };
            }

            if (eventType === 'done') {
                return {
                    finished: true,
                    messageCount: 1,
                    finishReason: payload.choices?.[0]?.finish_reason || 'stop',
                };
            }

            if (eventType === 'cancelled') {
                state.isCancellingChat = false;
                callbacks.syncChatControlLocks();
                chatRendering.setAssistantStatus(assistantMessage, 'Stopped', 'done');
                return { finished: true, messageCount: 0 };
            }

            if (eventType === 'error') {
                const errorDelta = payload.choices?.[0]?.delta?.content;
                if (errorDelta) {
                    assistantMessage.fullText += `\n\n${errorDelta}`;
                    if (render) {
                        chatRendering.renderAssistantMarkdown(assistantMessage);
                    }
                }
                assistantMessage.errorMessages.push(errorDelta || 'Unknown streaming error.');
                chatRendering.setAssistantStatus(assistantMessage, 'Something went wrong', 'error');
                return { finished: true, messageCount: 0 };
            }

            return { finished: false, messageCount: 0 };
        }

        function applyConsumedChatStreamEvent(
            payload,
            assistantMessage,
            { currentTaskId, lastSequence, messageCount, finishReason },
            options = {}
        ) {
            const nextSequence = Number.isInteger(payload.sequence)
                ? Math.max(lastSequence, payload.sequence)
                : lastSequence;
            const result = applyChatStreamPayload(payload, assistantMessage, options);
            return {
                currentTaskId: result.nextTaskId || currentTaskId,
                lastSequence: result.nextTaskId ? 0 : nextSequence,
                messageCount: Math.max(messageCount, result.messageCount),
                finishReason: result.finishReason || finishReason,
                finished: Boolean(result.finished),
                eventGap: Boolean(result.eventGap),
                redirected: Boolean(result.nextTaskId),
                receivedSequence: Number.isInteger(payload.sequence),
            };
        }

        async function fetchChatTaskReplaySnapshot(taskId, signal) {
            const response = await fetch(
                `api/chat/tasks/${encodeURIComponent(taskId)}/replay-snapshot`,
                { cache: 'no-store', signal }
            );
            if (response.status === 404 || response.status === 410) {
                return null;
            }
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            return response.json();
        }

        async function hydrateChatTaskReplaySnapshot(
            taskId,
            assistantMessage,
            abortController,
            { reset = false } = {}
        ) {
            const snapshot = await fetchChatTaskReplaySnapshot(taskId, abortController.signal);
            if (!snapshot) return null;
            if (snapshot.available === false) return null;
            if (reset) {
                chatRendering.resetAssistantStream(assistantMessage, {
                    render: false,
                    showReconnectStatus: false,
                });
            }

            let accumulator = {
                currentTaskId: taskId,
                lastSequence: 0,
                messageCount: 0,
                finishReason: '',
            };
            let finished = false;
            let eventGap = false;
            let redirected = false;
            for (const payload of snapshot.events || []) {
                const transition = applyConsumedChatStreamEvent(
                    payload,
                    assistantMessage,
                    accumulator,
                    { render: false }
                );
                accumulator = transition;
                finished = transition.finished;
                eventGap = transition.eventGap;
                redirected = transition.redirected;
            }
            chatRendering.renderAssistantMarkdown(assistantMessage);
            return {
                ...accumulator,
                lastSequence: redirected ? 0 : Math.max(0, Number(snapshot.latest_sequence) || 0),
                finished,
                eventGap,
                redirected,
            };
        }

        async function consumeChatTaskEvents(
            taskId,
            assistantMessage,
            abortController,
            initialState = {}
        ) {
            let currentTaskId = initialState.currentTaskId || taskId;
            let lastSequence = initialState.lastSequence || 0;
            let messageCount = initialState.messageCount || 0;
            let finished = Boolean(initialState.finished);
            let finishReason = initialState.finishReason || '';
            let reconnectAttempts = 0;
            let eventGap = false;

            async function recoverExpiredCursor() {
                let recovered;
                try {
                    recovered = await hydrateChatTaskReplaySnapshot(
                        currentTaskId,
                        assistantMessage,
                        abortController,
                        { reset: true }
                    );
                } catch (error) {
                    if (abortController.signal.aborted || error.name === 'AbortError') throw error;
                    console.warn('Could not hydrate expired chat cursor; waiting for durable completion.', error);
                    return null;
                }
                if (!recovered) return null;
                currentTaskId = recovered.currentTaskId;
                lastSequence = recovered.lastSequence;
                messageCount = Math.max(messageCount, recovered.messageCount);
                finishReason = recovered.finishReason || finishReason;
                finished = recovered.finished;
                eventGap = false;
                reconnectAttempts = 0;
                if (recovered.redirected) {
                    state.activeChatTaskId = currentTaskId;
                }
                return recovered;
            }

            while (!finished) {
                let redirected = false;
                const streamUrl = `api/chat/tasks/${encodeURIComponent(currentTaskId)}/events?after_sequence=${lastSequence}`;
                let response;
                try {
                    response = await fetch(streamUrl, {
                        method: 'GET',
                        signal: abortController.signal,
                        headers: { Accept: 'text/event-stream' }
                    });
                } catch (error) {
                    if (abortController.signal.aborted || reconnectAttempts >= CHAT_STREAM_MAX_RECONNECTS) {
                        throw error;
                    }
                    reconnectAttempts += 1;
                    chatRendering.setAssistantStatus(assistantMessage, 'Reconnecting…', 'thinking');
                    await waitForChatStreamReconnect(reconnectAttempts, abortController.signal);
                    continue;
                }
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    if (response.status === 410) {
                        const recovered = await recoverExpiredCursor();
                        if (recovered) {
                            redirected = recovered.redirected;
                            if (finished) break;
                            continue;
                        }
                        eventGap = true;
                        chatRendering.resetAssistantStream(assistantMessage);
                        chatRendering.setAssistantStatus(assistantMessage, 'Finishing in background…', 'thinking');
                        await waitForChatTaskTerminal(currentTaskId, abortController.signal);
                        break;
                    }
                    if (
                        (response.status === 408 || response.status === 429 || response.status >= 500)
                        && reconnectAttempts < CHAT_STREAM_MAX_RECONNECTS
                    ) {
                        reconnectAttempts += 1;
                        chatRendering.setAssistantStatus(assistantMessage, 'Reconnecting…', 'thinking');
                        await waitForChatStreamReconnect(reconnectAttempts, abortController.signal);
                        continue;
                    }
                    throw new Error(errorData.message || `HTTP ${response.status}`);
                }
                if (!response.body || !response.body.getReader) {
                    throw new Error('Streaming responses are not supported in this browser.');
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                try {
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;

                        buffer += decoder.decode(value, { stream: true });
                        const events = buffer.split(/\r?\n\r?\n/);
                        buffer = events.pop() || '';

                        for (const rawEvent of events) {
                            const payload = parseSseEvent(rawEvent);
                            if (!payload) continue;
                            const transition = applyConsumedChatStreamEvent(
                                payload,
                                assistantMessage,
                                { currentTaskId, lastSequence, messageCount, finishReason }
                            );
                            currentTaskId = transition.currentTaskId;
                            lastSequence = transition.lastSequence;
                            messageCount = transition.messageCount;
                            finishReason = transition.finishReason;
                            finished = transition.finished;
                            eventGap = transition.eventGap;
                            redirected = transition.redirected;
                            if (transition.receivedSequence) {
                                reconnectAttempts = 0;
                            }
                            if (redirected) {
                                state.activeChatTaskId = currentTaskId;
                                reconnectAttempts = 0;
                            }
                            if (finished || eventGap || redirected) break;
                        }
                        if (redirected) {
                            await reader.cancel();
                            break;
                        }
                        if (finished || eventGap) break;
                    }
                } catch (error) {
                    if (abortController.signal.aborted || reconnectAttempts >= CHAT_STREAM_MAX_RECONNECTS) {
                        throw error;
                    }
                    reconnectAttempts += 1;
                    chatRendering.setAssistantStatus(assistantMessage, 'Reconnecting…', 'thinking');
                    await waitForChatStreamReconnect(reconnectAttempts, abortController.signal);
                    continue;
                }

                if (redirected) continue;

                if (eventGap) {
                    const recovered = await recoverExpiredCursor();
                    if (recovered) {
                        if (finished) break;
                        continue;
                    }
                    await waitForChatTaskTerminal(currentTaskId, abortController.signal);
                    break;
                }

                if (!finished && buffer.trim()) {
                    const payload = parseSseEvent(buffer);
                    if (payload) {
                        const transition = applyConsumedChatStreamEvent(
                            payload,
                            assistantMessage,
                            { currentTaskId, lastSequence, messageCount, finishReason }
                        );
                        currentTaskId = transition.currentTaskId;
                        lastSequence = transition.lastSequence;
                        messageCount = transition.messageCount;
                        finishReason = transition.finishReason;
                        finished = transition.finished;
                        eventGap = transition.eventGap;
                        redirected = transition.redirected;
                        if (redirected) {
                            state.activeChatTaskId = currentTaskId;
                        }
                    }
                }

                if (redirected) continue;

                if (eventGap) {
                    const recovered = await recoverExpiredCursor();
                    if (recovered) {
                        if (finished) break;
                        continue;
                    }
                    await waitForChatTaskTerminal(currentTaskId, abortController.signal);
                    break;
                }

                if (!finished) {
                    const taskResponse = await fetch(`api/tasks/${encodeURIComponent(currentTaskId)}`, {
                        cache: 'no-store',
                        signal: abortController.signal
                    });
                    if (!taskResponse.ok) break;
                    const task = await taskResponse.json();
                    if (!['queued', 'running'].includes(task.status)) break;
                    await new Promise(resolve => setTimeout(resolve, 250));
                }
            }

            return { finished, messageCount, finishReason, eventGap };
        }

        function waitForChatStreamReconnect(attempt, signal) {
            const delayMs = CHAT_STREAM_RECONNECT_BASE_DELAY_MS * (2 ** (attempt - 1));
            return new Promise((resolve, reject) => {
                const timeoutId = window.setTimeout(() => {
                    signal.removeEventListener('abort', onAbort);
                    resolve();
                }, delayMs);
                const onAbort = () => {
                    window.clearTimeout(timeoutId);
                    reject(new DOMException('Chat stream reconnect cancelled.', 'AbortError'));
                };
                if (signal.aborted) {
                    onAbort();
                    return;
                }
                signal.addEventListener('abort', onAbort, { once: true });
            });
        }

        function releaseActiveChatStream(abortController) {
            if (state.activeChatAbortController !== abortController) return false;
            state.isLoading = false;
            state.isCancellingChat = false;
            state.activeChatSessionId = null;
            state.activeChatTaskId = null;
            state.activeChatAbortController = null;
            return true;
        }

        async function waitForChatTaskTerminal(taskId, signal) {
            let pollFailures = 0;
            while (!signal.aborted) {
                try {
                    const response = await fetch(`api/tasks/${encodeURIComponent(taskId)}`, {
                        cache: 'no-store',
                        signal,
                    });
                    if (!response.ok) {
                        throw new Error(`Could not read background chat task: HTTP ${response.status}`);
                    }
                    const task = await response.json();
                    pollFailures = 0;
                    if (!['queued', 'running'].includes(task.status)) return;
                } catch (error) {
                    if (signal.aborted) throw error;
                    pollFailures += 1;
                    if (pollFailures >= CHAT_STREAM_MAX_RECONNECTS) {
                        throw new Error('Could not confirm that the background chat task finished.');
                    }
                }
                await waitForChatStreamReconnect(2, signal);
            }
        }

        return Object.freeze({
            consumeEvents: consumeChatTaskEvents,
            hydrateReplaySnapshot: hydrateChatTaskReplaySnapshot,
            releaseActiveStream: releaseActiveChatStream,
        });
    }

    window.ChatTaskStream = Object.freeze({
        create: createChatTaskStreamController,
    });
})(window);
