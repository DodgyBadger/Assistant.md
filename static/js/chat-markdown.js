/** Markdown, sanitization, math, and post-processing for chat content. */
(function chatMarkdownModule(window, document) {
    function createChatMarkdownController({ utils, callbacks }) {
        let mathTypesetQueue = Promise.resolve();

            function enforceExternalLinkBehavior(container) {
                if (!container) return;
                const links = container.querySelectorAll('a[href]');
                links.forEach(link => {
                    link.setAttribute('target', '_blank');
                    link.setAttribute('rel', 'noopener noreferrer');
                });
            }

            function renderAssistantHtml(bodyDiv, markdownContent = '', { softBreaks = false } = {}) {
                if (!bodyDiv) return;
                const content = (markdownContent || '').trim();
                const protectedContent = protectLatexForMarkdown(content);
                const renderedHtml = content
                    ? marked.parse(protectedContent.markdown, { breaks: softBreaks })
                    : '';
                const restoredHtml = restoreLatexPlaceholders(renderedHtml, protectedContent.segments);
                const sanitizedHtml = sanitizeAssistantHtml(restoredHtml);
                if (sanitizedHtml === null) {
                    bodyDiv.textContent = content;
                    return;
                }
                bodyDiv.innerHTML = sanitizedHtml;
            }

            function renderMarkdownPreview(container, markdownContent = '', options = {}) {
                renderAssistantHtml(container, markdownContent, options);
                postProcessAssistantBody(container, { decorateVaultTags: true });
            }

            function protectLatexForMarkdown(markdown) {
                if (!markdown) {
                    return { markdown: '', segments: [] };
                }

                const segments = [];
                const codePattern = /(```[\s\S]*?```|`[^`\n]*`)/g;
                let cursor = 0;
                let output = '';
                let match = codePattern.exec(markdown);

                while (match) {
                    output += replaceLatexSegments(markdown.slice(cursor, match.index), segments);
                    output += match[0];
                    cursor = match.index + match[0].length;
                    match = codePattern.exec(markdown);
                }

                output += replaceLatexSegments(markdown.slice(cursor), segments);
                return { markdown: output, segments };
            }

            function replaceLatexSegments(text, segments) {
                if (!text) return '';

                const pattern =
                    /(\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\))/g;

                return text.replace(pattern, (rawMath) => {
                    const placeholder = `@@MATH_SEGMENT_${segments.length}@@`;
                    const display = rawMath.startsWith('\\[');
                    const tex = rawMath.slice(2, -2);
                    segments.push({ tex, display });
                    return placeholder;
                });
            }

            function restoreLatexPlaceholders(html, segments) {
                if (!html || !segments.length) return html;

                return segments.reduce((acc, segment, index) => {
                    const placeholder = `@@MATH_SEGMENT_${index}@@`;
                    const display = segment.display ? 'true' : 'false';
                    const mathHtml = `<span class="assistant-latex-segment" data-math-display="${display}">${utils.escapeHtml(segment.tex)}</span>`;
                    return acc.split(placeholder).join(mathHtml);
                }, html);
            }

            function getMathJax() {
                if (typeof window === 'undefined') return null;
                const mathJax = window.MathJax;
                if (!mathJax || typeof mathJax.tex2chtmlPromise !== 'function') return null;
                return mathJax;
            }

            function sanitizeAssistantHtml(html) {
                if (!html) return '';

                if (!window.DOMPurify || typeof window.DOMPurify.sanitize !== 'function') {
                    return null;
                }

                return window.DOMPurify.sanitize(html, {
                    USE_PROFILES: { html: true }
                });
            }

            function postProcessAssistantBody(bodyDiv, { decorateVaultTags = false } = {}) {
                if (!bodyDiv) return;
                enforceExternalLinkBehavior(bodyDiv);
                renderAssistantMath(bodyDiv);
                callbacks.attachCodeCopyButtons(bodyDiv);
                if (decorateVaultTags) {
                    decorateVaultMarkdownTags(bodyDiv);
                }
                callbacks.enhanceFileLinks?.(bodyDiv);
            }

            function decorateVaultMarkdownTags(container) {
                const textNodes = [];
                const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
                    acceptNode(node) {
                        const parent = node.parentElement;
                        if (!parent || parent.closest(
                            'a, button, code, pre, textarea, .assistant-latex-segment, .vault-markdown-tag'
                        )) {
                            return NodeFilter.FILTER_REJECT;
                        }
                        return vaultTagMatches(node.textContent || '').length
                            ? NodeFilter.FILTER_ACCEPT
                            : NodeFilter.FILTER_REJECT;
                    },
                });
                while (walker.nextNode()) {
                    textNodes.push(walker.currentNode);
                }
                textNodes.forEach(decorateVaultTagTextNode);
            }

            function decorateVaultTagTextNode(node) {
                const text = node.textContent || '';
                const matches = vaultTagMatches(text);
                if (!matches.length) return;

                let cursor = 0;
                const fragment = document.createDocumentFragment();
                matches.forEach(({ start, end, value }) => {
                    if (start > cursor) {
                        fragment.appendChild(document.createTextNode(text.slice(cursor, start)));
                    }
                    const tag = document.createElement('span');
                    tag.className = 'vault-markdown-tag';
                    tag.textContent = value;
                    fragment.appendChild(tag);
                    cursor = end;
                });
                if (cursor < text.length) {
                    fragment.appendChild(document.createTextNode(text.slice(cursor)));
                }
                node.parentNode?.replaceChild(fragment, node);
            }

            function vaultTagMatches(text) {
                const matches = [];
                const pattern =
                    /(^|[\s([{"'“‘>])#([\p{L}\p{M}\p{N}_-]+(?:\/[\p{L}\p{M}\p{N}_-]+)*)(?![\p{L}\p{M}\p{N}_/-])/gu;
                for (const match of text.matchAll(pattern)) {
                    const tagBody = match[2] || '';
                    if (!/[\p{L}\p{M}_-]/u.test(tagBody)) continue;
                    const prefixLength = (match[1] || '').length;
                    const start = (match.index || 0) + prefixLength;
                    const value = `#${tagBody}`;
                    matches.push({ start, end: start + value.length, value });
                }
                return matches;
            }

            function renderAssistantMath(bodyDiv) {
                if (!bodyDiv) return;
                const mathJax = getMathJax();
                if (!mathJax || typeof mathJax.tex2chtmlPromise !== 'function') return;
                const mathNodes = Array.from(bodyDiv.querySelectorAll('.assistant-latex-segment:not(.assistant-latex-rendered)'));
                if (!mathNodes.length) return;

                mathTypesetQueue = mathTypesetQueue
                    .then(() => mathJax.startup?.promise)
                    .then(() => {
                        const conversions = mathNodes.map((node) => {
                            const tex = node.textContent || '';
                            const display = node.dataset.mathDisplay === 'true';
                            return mathJax.tex2chtmlPromise(tex, { display })
                                .then((mathNode) => {
                                    node.replaceChildren(mathNode);
                                    node.classList.add('assistant-latex-rendered');
                                })
                                .catch((error) => {
                                    const open = display ? '\\[' : '\\(';
                                    const close = display ? '\\]' : '\\)';
                                    node.textContent = `${open}${tex}${close}`;
                                    console.warn('MathJax render failed:', error);
                                });
                        });
                        return Promise.all(conversions);
                    })
                    .catch((error) => {
                        console.warn('MathJax render failed:', error);
                    });
            }

            function scheduleAssistantPostProcess(context, delayMs = 120) {
                if (!context || !context.bodyDiv) return;

                if (context.postProcessTimer) {
                    clearTimeout(context.postProcessTimer);
                }

                context.postProcessTimer = window.setTimeout(() => {
                    postProcessAssistantBody(context.bodyDiv);
                    context.postProcessTimer = null;
                    callbacks.scrollChatToBottom();
                }, delayMs);
            }

            function flushAssistantPostProcess(context) {
                if (!context || !context.bodyDiv) return;

                if (context.postProcessTimer) {
                    clearTimeout(context.postProcessTimer);
                    context.postProcessTimer = null;
                }

                postProcessAssistantBody(context.bodyDiv);
            }

        return Object.freeze({
            flushPostProcess: flushAssistantPostProcess,
            postProcess: postProcessAssistantBody,
            renderHtml: renderAssistantHtml,
            renderPreview: renderMarkdownPreview,
            schedulePostProcess: scheduleAssistantPostProcess,
        });
    }

    window.ChatMarkdown = Object.freeze({
        create: createChatMarkdownController,
    });
})(window, document);
