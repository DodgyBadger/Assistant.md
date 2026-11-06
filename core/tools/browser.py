"""
Browser tool backed by Playwright for resilient page extraction.

Provides conservative browser-driven extraction for pages where simple crawlers
or extract APIs fail, especially on JavaScript-heavy sites. Browser policy is
deliberately narrow: downloads are blocked, local/private network targets are
blocked, and browser state is isolated per call.
"""

from __future__ import annotations

import asyncio
import os
from collections import OrderedDict
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any, Literal
from urllib.parse import urlsplit

from markdownify import markdownify as html_to_markdown
from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.logger import UnifiedLogger
from core.runtime.execution_tasks import get_current_execution_task
from core.runtime.resources import read_cgroup_memory_status
from core.settings import (
    get_browser_max_calls_per_turn,
    get_browser_max_concurrent_sessions,
    get_browser_min_memory_headroom_bytes,
    get_browser_navigation_timeout_seconds,
    get_browser_selector_timeout_seconds,
    get_default_api_timeout,
)
from core.web.errors import WebUrlPolicyError
from core.web.security import (
    resolve_public_url,
    sanitize_url_for_log,
    sanitize_urls_in_text_for_log,
)

from .base import BaseTool, ToolRecoveryPolicy
from .failures import classify_exception, tool_failure_return

logger = UnifiedLogger(tag="browser-tool")


class BrowserTool(BaseTool):
    """Playwright-backed browser tool for targeted extraction with strict network guards."""

    _MAX_LINKS = 20
    _ROOT_SELECTORS = ("main", "article", "[role='main']", "body")
    _FALLBACK_CANDIDATE_SELECTORS = ("section", "div", "article", "main", "table", "td")
    _VALID_WAIT_UNTIL = {"load", "domcontentloaded", "networkidle"}
    _BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
    _ALLOWED_HTTP_METHODS = {"GET", "HEAD"}
    _MAX_EXTRACTED_TEXT_CHARS = 40_000
    _MAX_FALLBACK_HTML_CHARS = 120_000
    _MIN_PRIMARY_TEXT_CHARS = 500
    _MAX_TRACKED_CALL_SCOPES = 1024
    _session_semaphore: BoundedSemaphore | None = None
    _session_semaphore_limit = 0
    _active_sessions = 0
    _session_state_lock = Lock()
    _call_counts: OrderedDict[str, int] = OrderedDict()
    _call_counts_lock = Lock()

    @staticmethod
    def _get_process_rss_bytes() -> int | None:
        """Return the current process RSS in bytes when available."""
        status_path = Path("/proc/self/status")
        try:
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if not line.startswith("VmRSS:"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    return None
                return int(parts[1]) * 1024
        except OSError:
            return None
        return None

    @classmethod
    def _log_event(cls, event: str, **data: Any) -> None:
        """Emit browser lifecycle events to both activity and validation logs."""
        original_urls: list[tuple[str, str]] = []
        for key, value in data.items():
            if isinstance(value, str) and (key == "url" or key.endswith("_url")):
                original_urls.append((value, sanitize_url_for_log(value)))
                data[key] = sanitize_url_for_log(value)
        error = data.get("error")
        if isinstance(error, str):
            for original, sanitized in original_urls:
                error = error.replace(original, sanitized)
            data["error"] = error
        goal = data.pop("goal", None)
        if isinstance(goal, str) and goal:
            data["goal_chars"] = len(goal)
        rss_bytes = cls._get_process_rss_bytes()
        if rss_bytes is not None:
            data.setdefault("memory_rss_bytes", rss_bytes)
        memory = read_cgroup_memory_status()
        if memory.current_bytes is not None:
            data.setdefault("cgroup_memory_current_bytes", memory.current_bytes)
        if memory.max_bytes is not None:
            data.setdefault("cgroup_memory_max_bytes", memory.max_bytes)
        if memory.events:
            data.setdefault("cgroup_memory_events", memory.events)
        logger.add_sink("validation").info(event, data=data)

    @classmethod
    def get_tool(cls, vault_path: str | None = None) -> Tool:
        """Get the Playwright-backed browser tool."""

        async def browser(
            *,
            url: str = "",
            goal: str = "",
            wait_until: str = "domcontentloaded",
            wait_for_selector: str = "",
            extract_selector: str = "",
            include_links: bool = False,
            interactive: bool = False,
            operation: Literal["read", "click", "fill", "select"] = "read",
            element_id: int = 0,
            value: str = "",
            request_human: bool = False,
            reason: str = "",
        ) -> str | ToolReturn:
            """Open a page in a headless browser and extract compact page content.

                        :param url: Page URL to open
                        :param goal: Brief extraction goal to guide target selection
                        :param wait_until: Navigation readiness event
                        :param wait_for_selector: Optional selector to wait for after navigation
                        :param extract_selector: Optional selector to extract instead of the main content area
                        :param include_links: Include a short list of visible links from the extracted region
                        :param interactive: Opt into a persistent headed browser after ordinary extraction fails
                        :param operation: Interactive action; read returns current page text and element IDs
                        :param element_id: Element ID from the most recent interactive read
                        :param value: Value for interactive fill or select
                        :param request_human: Show an Open browser link and wait for the human to return control
                        :param reason: Brief explanation of the sign-in or CAPTCHA step the human must complete

            Default extraction policy notes (interactive prototype allows forms):
                        - Only public http/https URLs are allowed by default; `data:` is allowed for testing.
                        - Redirects or subrequests to local/private network targets are blocked.
                        - Only read-oriented HTTP methods are allowed (`GET`, `HEAD`).
                        - Downloads are blocked.
                        - Browser state is isolated per call.
            """
            try:
                if os.environ.get("ASSISTANTMD_INTERACTIVE_BROWSER") == "1":
                    from experiments.browser_handoff.integration import browse, browsers

                    if interactive or request_human or browsers.active():
                        cls._log_event(
                            "interactive_browser_invoked",
                            tool="browser",
                            url=url,
                            operation=operation,
                            request_human=request_human,
                        )
                        return await browse(
                            url=url,
                            operation=operation,
                            element_id=element_id,
                            value=value,
                            request_human=request_human,
                            reason=reason,
                        )
                elif interactive or request_human:
                    raise RuntimeError(
                        "Start the development app with scripts/dev run --interactive-browser to enable this prototype."
                    )
                cls._record_scoped_call()
                cls._log_event(
                    "tool_invoked",
                    tool="browser",
                    url=url,
                    goal=goal.strip() or None,
                    wait_until=wait_until,
                    wait_for_selector=wait_for_selector.strip() or None,
                    extract_selector=extract_selector.strip() or None,
                    include_links=include_links,
                )
                semaphore = await cls._acquire_session_slot()
                try:
                    cls._assert_memory_headroom()
                    return await cls._browse(
                        url=url,
                        goal=goal,
                        wait_until=wait_until,
                        wait_for_selector=wait_for_selector,
                        extract_selector=extract_selector,
                        include_links=include_links,
                    )
                finally:
                    cls._release_session_slot(semaphore)
            except Exception as exc:  # pylint: disable=broad-except
                safe_error = sanitize_urls_in_text_for_log(str(exc)).replace(
                    url, sanitize_url_for_log(url)
                )
                cls._log_event(
                    "browser_navigation_failed",
                    tool="browser",
                    url=url,
                    result_type=cls._extract_result_type(safe_error),
                    error=safe_error,
                )
                safe_exc = RuntimeError(safe_error)
                return tool_failure_return(
                    tool_name="browser",
                    message=cls._format_error(safe_error),
                    classification=classify_exception(
                        safe_exc, phase="browser_navigation"
                    ),
                    metadata={"url": sanitize_url_for_log(url)},
                )

        return Tool(
            browser,
            name="browser",
            description=(
                "Open a dynamic web page in a headless browser and extract content "
                "from the main page region. Prefer web_extract for ordinary static pages."
                " If extraction fails, retry with interactive=true for a persistent browser."
                " Interactive calls return page text and element IDs for read/click/fill/select."
                " Omit url to continue on the current page; the interactive session is reused."
                " For CAPTCHA or sign-in, call request_human=true with a reason; the chat"
                " shows an Open browser link and this call waits for handback, then resumes."
                " Never ask the user to put passwords in chat or tool arguments."
            ),
        )

    @classmethod
    async def _acquire_session_slot(cls) -> BoundedSemaphore:
        """Acquire one process-wide Chromium slot without blocking the event loop."""
        while True:
            limit = get_browser_max_concurrent_sessions()
            with cls._session_state_lock:
                if cls._session_semaphore is None or (
                    cls._session_semaphore_limit != limit and cls._active_sessions == 0
                ):
                    cls._session_semaphore = BoundedSemaphore(limit)
                    cls._session_semaphore_limit = limit
                semaphore = cls._session_semaphore
                if semaphore.acquire(blocking=False):
                    cls._active_sessions += 1
                    return semaphore
            await asyncio.sleep(0.05)

    @classmethod
    def _release_session_slot(cls, semaphore: BoundedSemaphore) -> None:
        with cls._session_state_lock:
            semaphore.release()
            cls._active_sessions = max(0, cls._active_sessions - 1)

    @classmethod
    def _record_scoped_call(cls) -> None:
        limit = get_browser_max_calls_per_turn()
        if limit <= 0:
            return
        execution_task = get_current_execution_task()
        if execution_task is not None:
            scope = execution_task.task_id
        else:
            current = asyncio.current_task()
            scope = f"unscoped:{id(current)}"
        with cls._call_counts_lock:
            count = cls._call_counts.pop(scope, 0) + 1
            cls._call_counts[scope] = count
            while len(cls._call_counts) > cls._MAX_TRACKED_CALL_SCOPES:
                cls._call_counts.popitem(last=False)
        if count > limit:
            cls._log_event(
                "browser_launch_refused",
                tool="browser",
                reason="task_call_limit",
                call_count=count,
                call_limit=limit,
            )
            raise RuntimeError(
                f"Browser call limit exceeded for this execution task ({limit}). "
                "Use web_extract for static pages or continue in a new scoped task."
            )

    @classmethod
    def _assert_memory_headroom(cls) -> None:
        required = get_browser_min_memory_headroom_bytes()
        if required <= 0:
            return
        memory = read_cgroup_memory_status()
        available = memory.available_bytes
        if available is None or available >= required:
            return
        cls._log_event(
            "browser_launch_refused",
            tool="browser",
            reason="memory_headroom",
            available_bytes=available,
            required_bytes=required,
        )
        raise RuntimeError(
            "Insufficient container memory headroom for Chromium launch: "
            f"{available // (1024 * 1024)} MiB available, "
            f"{required // (1024 * 1024)} MiB required."
        )

    @classmethod
    async def _browse(
        cls,
        *,
        url: str,
        goal: str,
        wait_until: str,
        wait_for_selector: str,
        extract_selector: str,
        include_links: bool,
    ) -> str:
        cls._validate_url(url)
        normalized_wait_until = cls._validate_wait_until(wait_until)

        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Add the `playwright` package and run "
                "`playwright install chromium` on the host."
            ) from exc

        timeout_ms, selector_timeout_ms = cls._get_timeout_settings()

        try:
            async with async_playwright() as playwright:
                extraction = await cls._run_browser_session(
                    playwright=playwright,
                    url=url,
                    normalized_wait_until=normalized_wait_until,
                    timeout_ms=timeout_ms,
                    selector_timeout_ms=selector_timeout_ms,
                    wait_for_selector=wait_for_selector,
                    extract_selector=extract_selector,
                    include_links=include_links,
                )
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"result_type: timeout\nnavigation timed out after {timeout_ms} ms"
            ) from exc
        except PlaywrightError as exc:
            raise cls._translate_playwright_error(exc) from exc

        cls._log_event(
            "browser_navigation_succeeded",
            tool="browser",
            url=url,
            final_url=extraction["final_url"],
            status_code=extraction["status_code"],
        )
        cls._log_event(
            "browser_extraction_succeeded",
            tool="browser",
            url=url,
            selector=extraction["selector"],
            root_strategy=extraction["root_strategy"],
            title=extraction["title"],
            include_links=include_links,
            content_chars=len(extraction["content"]),
            link_count=len(extraction["links"]),
        )

        return cls._format_result(
            url=url,
            final_url=extraction["final_url"],
            status_code=extraction["status_code"],
            title=extraction["title"],
            selector=extraction["selector"],
            goal=goal,
            content=extraction["content"],
            links=extraction["links"],
        )

    @classmethod
    def _get_timeout_settings(cls) -> tuple[int, int]:
        """Return bounded navigation and selector timeouts in milliseconds."""
        navigation_timeout_ms = min(
            max(int(get_default_api_timeout() * 1000), 1_000),
            max(int(get_browser_navigation_timeout_seconds() * 1000), 1_000),
        )
        selector_timeout_ms = min(
            navigation_timeout_ms,
            max(int(get_browser_selector_timeout_seconds() * 1000), 1_000),
        )
        return navigation_timeout_ms, selector_timeout_ms

    @classmethod
    async def _run_browser_session(
        cls,
        *,
        playwright: Any,
        url: str,
        normalized_wait_until: str,
        timeout_ms: int,
        selector_timeout_ms: int,
        wait_for_selector: str,
        extract_selector: str,
        include_links: bool,
    ) -> dict[str, Any]:
        """Launch a browser session, navigate, extract a region, and return structured results."""
        # Use Chromium's newer headless mode for closer parity with full browser behavior.
        cls._log_event("browser_session_launch_started", tool="browser", url=url)
        browser_instance = await playwright.chromium.launch(
            headless=True,
            channel="chromium",
        )
        context = None
        try:
            cls._log_event("browser_session_launch_completed", tool="browser", url=url)
            context = await browser_instance.new_context(accept_downloads=False)
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)
            await page.route("**/*", cls._route_request)
            response = await page.goto(
                url,
                wait_until=normalized_wait_until,
                timeout=timeout_ms,
            )
            cls._log_event(
                "browser_navigation_response_received",
                tool="browser",
                url=url,
                final_url=page.url,
                status_code=response.status if response else None,
            )
            if wait_for_selector.strip():
                await cls._wait_for_selector(
                    page=page,
                    selector=wait_for_selector.strip(),
                    timeout_ms=selector_timeout_ms,
                )

            locator, target_selector, root_strategy = await cls._resolve_target_region(
                page=page,
                extract_selector=extract_selector,
            )
            cls._log_event(
                "browser_extraction_started",
                tool="browser",
                url=url,
                selector=target_selector,
                root_strategy=root_strategy,
                include_links=include_links,
            )
            extracted = await cls._extract_region(
                locator=locator,
                include_links=include_links,
            )
            final_url = page.url
            cls._validate_url(final_url)
            cls._log_event(
                "browser_extraction_completed",
                tool="browser",
                url=url,
                final_url=final_url,
                selector=target_selector,
                root_strategy=root_strategy,
                content_chars=len(extracted["content"]),
                link_count=len(extracted["links"]),
            )
            return {
                "final_url": final_url,
                "status_code": response.status if response else None,
                "title": (await page.title()).strip(),
                "selector": target_selector,
                "root_strategy": root_strategy,
                "content": extracted["content"],
                "links": extracted["links"],
            }
        finally:
            if context is not None:
                await context.close()
            await browser_instance.close()
            cls._log_event("browser_session_closed", tool="browser", url=url)

    @classmethod
    async def _resolve_target_region(
        cls,
        *,
        page: Any,
        extract_selector: str,
    ) -> tuple[Any, str, str]:
        """Resolve the locator and metadata for the extraction target."""
        if extract_selector.strip():
            target_selector = extract_selector.strip()
            await cls._ensure_selector_present(
                page=page,
                selector=target_selector,
            )
            return (
                page.locator(target_selector).first,
                target_selector,
                "explicit_selector",
            )
        return await cls._resolve_best_root(page)

    @staticmethod
    def _translate_playwright_error(exc: Exception) -> RuntimeError:
        """Map Playwright errors into stable tool-facing error messages."""
        message = str(exc)
        if "Executable doesn't exist" in message:
            return RuntimeError(
                "result_type: error\nBrowser runtime is missing. Install the Playwright "
                "Chromium bundle for this environment with `python -m playwright install "
                "--with-deps chromium`, or rebuild the container image with browser "
                "binaries included."
            )
        if "Download is starting" in message:
            return RuntimeError(
                "result_type: download\nURL initiates a download instead of rendering an HTML page. "
                "Try a different document URL; selector retries will not help."
            )
        if "ERR_BLOCKED_BY_CLIENT" in message:
            return RuntimeError(
                "result_type: blocked\nNavigation was blocked by browser policy. "
                "Local/private network targets and related redirects are not allowed."
            )
        return RuntimeError(f"result_type: error\n{message}")

    @classmethod
    async def _resolve_best_root(cls, page: Any) -> tuple[Any, str, str]:
        """Choose the best extraction root using semantic selectors first, then a content heuristic."""
        for selector in cls._ROOT_SELECTORS[:-1]:
            locator = page.locator(selector).first
            if await locator.count():
                return locator, selector, "semantic_selector"

        best_handle = await page.evaluate_handle(
            """(candidateSelectors) => {
                const isVisible = (el) => {
                    const style = window.getComputedStyle(el);
                    if (!style) return true;
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };

                const normalize = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                const seen = new Set();
                let best = null;

                for (const selector of candidateSelectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        if (seen.has(el) || !isVisible(el)) continue;
                        seen.add(el);

                        const text = normalize(el.innerText || '');
                        if (text.length < 400) continue;

                        const links = el.querySelectorAll('a[href]').length;
                        const paras = el.querySelectorAll('p').length;
                        const headings = el.querySelectorAll('h1,h2,h3,h4,h5,h6').length;
                        const controls = el.querySelectorAll('button,input,select,textarea').length;
                        const idClass = `${el.id || ''} ${typeof el.className === 'string' ? el.className : ''}`.toLowerCase();
                        const textNodes = Math.max(text.length / 80, 1);
                        const linkDensity = links / textNodes;

                        let score = text.length;
                        score += paras * 180;
                        score += headings * 220;
                        score -= links * 18;
                        score -= controls * 120;
                        score -= linkDensity * 900;
                        score -= Math.max(0, links - 80) * 45;
                        score -= Math.max(0, headings - 40) * 35;

                        if (paras === 0 && links > 50) score -= 3000;

                        if (el.tagName === 'ARTICLE' || el.tagName === 'MAIN') score += 800;
                        if (idClass.includes('content')) score += 500;
                        if (idClass.includes('article')) score += 400;
                        if (idClass.includes('section')) score += 250;

                        if (
                            idClass.includes('nav') ||
                            idClass.includes('menu') ||
                            idClass.includes('sidebar') ||
                            idClass.includes('search') ||
                            idClass.includes('toolbar') ||
                            idClass.includes('breadcrumb') ||
                            idClass.includes('footer') ||
                            idClass.includes('header') ||
                            idClass.includes('toc') ||
                            idClass.includes('contents')
                        ) {
                            score -= 2200;
                        }

                        if (!best || score > best.score) {
                            best = { element: el, score };
                        }
                    }
                }

                return best ? best.element : document.body;
            }""",
            list(cls._FALLBACK_CANDIDATE_SELECTORS),
        )
        locator = page.locator("body")
        target_selector = "body"
        locator = best_handle.as_element()
        if locator is None:
            await best_handle.dispose()
            return page.locator("body").first, "body", "body_fallback"
        element_locator = locator
        tag_name = (
            await element_locator.evaluate("(node) => node.tagName.toLowerCase()")
        ).strip()
        element_id = (await element_locator.evaluate("(node) => node.id || ''")).strip()
        class_name = (
            await element_locator.evaluate(
                "(node) => typeof node.className === 'string' ? node.className : ''"
            )
        ).strip()
        target_selector = cls._describe_element_selector(
            tag_name, element_id, class_name
        )
        return element_locator, target_selector, "heuristic_fallback"

    @classmethod
    async def _extract_region(
        cls, *, locator: Any, include_links: bool
    ) -> dict[str, Any]:
        """Extract compact content and optional links from a selected page region."""
        payload = await locator.evaluate(
            """(node, options) => {
                const { includeLinks, maxTextChars, maxHtmlChars, minPrimaryTextChars } = options;
                const clone = node.cloneNode(true);
                clone.querySelectorAll('script,style,noscript,template').forEach(
                    (element) => element.remove()
                );
                const blockTags = new Set([
                    'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'BR', 'CAPTION', 'CODE',
                    'DD', 'DIV', 'DL', 'DT', 'FIELDSET', 'FIGCAPTION', 'FIGURE', 'FOOTER',
                    'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'HR', 'LI', 'MAIN',
                    'NAV', 'OL', 'P', 'PRE', 'SECTION', 'TABLE', 'TBODY', 'TD', 'TH', 'THEAD',
                    'TR', 'UL'
                ]);
                const normalizeWhitespace = (text) => (text || '')
                    .replace(/\\u00a0/g, ' ')
                    .replace(/\\r\\n?/g, '\\n')
                    .replace(/[ \\t]+/g, ' ')
                    .replace(/ *\\n */g, '\\n')
                    .replace(/\\n{3,}/g, '\\n\\n')
                    .trim();
                const appendTruncationNotice = (text, notice) => {
                    const base = normalizeWhitespace(text);
                    return base ? `${base}\\n\\n${notice}` : notice;
                };
                const extractStructuredText = (root) => {
                    const lines = [];
                    const pushLine = (value = '') => {
                        lines.push(value);
                    };
                    const visit = (current) => {
                        if (current.nodeType === Node.TEXT_NODE) {
                            const text = normalizeWhitespace(current.textContent || '');
                            if (text) {
                                lines.push(text);
                            }
                            return;
                        }
                        if (current.nodeType !== Node.ELEMENT_NODE) {
                            return;
                        }

                        const element = current;
                        const tagName = element.tagName || '';
                        if (tagName === 'BR') {
                            pushLine('');
                            return;
                        }
                        const isBlock = blockTags.has(tagName);
                        if (isBlock && lines.length && lines[lines.length - 1] !== '') {
                            pushLine('');
                        }

                        if (tagName === 'PRE') {
                            const preText = (element.textContent || '').replace(/\\r\\n?/g, '\\n').trim();
                            if (preText) {
                                lines.push('```');
                                lines.push(preText);
                                lines.push('```');
                            }
                        } else {
                            for (const child of element.childNodes) {
                                visit(child);
                            }
                        }

                        if (tagName === 'LI') {
                            let index = lines.length - 1;
                            while (index >= 0 && lines[index] === '') {
                                index--;
                            }
                            if (index >= 0 && !lines[index].startsWith('- ')) {
                                lines[index] = `- ${lines[index]}`;
                            }
                        }

                        if (isBlock && lines.length && lines[lines.length - 1] !== '') {
                            pushLine('');
                        }
                    };

                    visit(root);
                    return normalizeWhitespace(lines.join('\\n'));
                };
                let text = extractStructuredText(clone);
                let textTruncated = false;
                if (text.length > maxTextChars) {
                    text = text.slice(0, maxTextChars).trimEnd();
                    text = appendTruncationNotice(
                        text,
                        '*Content truncated for compact extraction.*'
                    );
                    textTruncated = true;
                }
                let html = '';
                let htmlTruncated = false;
                if (text.length < minPrimaryTextChars) {
                    html = (clone.innerHTML || '').trim();
                    if (html.length > maxHtmlChars) {
                        html = `${html.slice(0, maxHtmlChars).trimEnd()}<!-- truncated -->`;
                        htmlTruncated = true;
                    }
                }
                const links = includeLinks
                    ? Array.from(clone.querySelectorAll('a[href]'))
                        .slice(0, 20)
                        .map((anchor) => ({
                            text: (anchor.innerText || anchor.textContent || '').trim(),
                            href: anchor.href || anchor.getAttribute('href') || '',
                        }))
                        .filter((item) => item.href)
                    : [];
                return { html, htmlTruncated, links, text, textTruncated };
            }""",
            {
                "includeLinks": include_links,
                "maxTextChars": cls._MAX_EXTRACTED_TEXT_CHARS,
                "maxHtmlChars": cls._MAX_FALLBACK_HTML_CHARS,
                "minPrimaryTextChars": cls._MIN_PRIMARY_TEXT_CHARS,
            },
        )
        text = cls._clean_extracted_text(str(payload.get("text", "")).strip())
        html = str(payload.get("html", "")).strip()
        cls._log_event(
            "browser_extraction_payload_captured",
            tool="browser",
            text_chars=len(text),
            text_truncated=bool(payload.get("textTruncated")),
            html_chars=len(html),
            html_truncated=bool(payload.get("htmlTruncated")),
            raw_link_count=len(payload.get("links", [])),
            include_links=include_links,
        )
        content = text
        if len(content) < cls._MIN_PRIMARY_TEXT_CHARS and html:
            markdown = html_to_markdown(html, heading_style="ATX")
            content = cls._clean_extracted_text(markdown)
            if bool(payload.get("htmlTruncated")) and content:
                content = cls._append_truncation_notice(content)
        links = cls._format_links(payload.get("links", [])) if include_links else []
        if not content:
            content = "*No extractable content found in the selected region.*"
        return {"content": content, "links": links}

    @classmethod
    def _clean_extracted_text(cls, text: str) -> str:
        """Normalize extracted text for compact tool output."""
        compact = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        while "\n\n\n" in compact:
            compact = compact.replace("\n\n\n", "\n\n")
        return compact

    @staticmethod
    def _append_truncation_notice(text: str) -> str:
        """Append a compact truncation note when extracted content was capped."""
        notice = "*Content truncated for compact extraction.*"
        return f"{text}\n\n{notice}" if text else notice

    @classmethod
    def _format_links(cls, raw_links: list[dict[str, Any]]) -> list[str]:
        """Format extracted links into a compact markdown list."""
        lines: list[str] = []
        for item in raw_links[: cls._MAX_LINKS]:
            href = str(item.get("href", "")).strip()
            if not href:
                continue
            text = str(item.get("text", "")).strip() or href
            lines.append(f"- [{text}]({href})")
        return lines

    @classmethod
    def _format_result(
        cls,
        *,
        url: str,
        final_url: str,
        status_code: int | None,
        title: str,
        selector: str,
        goal: str,
        content: str,
        links: list[str],
    ) -> str:
        """Render extracted browser output as compact markdown."""
        lines = [
            "# Browser Extraction",
            "",
            "- Result type: success",
            f"- Requested URL: {url}",
            f"- Final URL: {final_url}",
            f"- Page title: {title or '(untitled)'}",
            f"- Extracted selector: {selector}",
            f"- HTTP status: {status_code if status_code is not None else 'N/A'}",
        ]
        if goal.strip():
            lines.append(f"- Goal: {goal.strip()}")

        lines.extend(["", "## Content", "", content])
        if links:
            lines.extend(["", "## Links", ""])
            lines.extend(links)
        return "\n".join(lines)

    @classmethod
    async def _route_request(cls, route: Any) -> None:
        """Abort blocked or non-essential requests to keep extraction safe and fast."""
        request_url = route.request.url
        method = (route.request.method or "GET").upper()
        if method not in cls._ALLOWED_HTTP_METHODS:
            cls._log_event(
                "browser_request_blocked",
                tool="browser",
                request_url=request_url,
                resource_type=route.request.resource_type,
                method=method,
                reason="HTTP method not allowed",
            )
            await route.abort("blockedbyclient")
            return
        try:
            cls._validate_url(request_url)
        except ValueError as exc:
            cls._log_event(
                "browser_request_blocked",
                tool="browser",
                request_url=request_url,
                resource_type=route.request.resource_type,
                method=method,
                reason=str(exc),
            )
            await route.abort("blockedbyclient")
            return
        if route.request.resource_type in cls._BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return
        await route.continue_()

    @classmethod
    async def _wait_for_selector(
        cls,
        *,
        page: Any,
        selector: str,
        timeout_ms: int,
    ) -> None:
        """Wait briefly for a requested selector and fail with guidance if absent."""
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
        except Exception as exc:
            raise await cls._selector_not_found_error(
                page=page,
                parameter_name="wait_for_selector",
                selector=selector,
            ) from exc

    @classmethod
    async def _ensure_selector_present(
        cls,
        *,
        page: Any,
        selector: str,
    ) -> None:
        """Verify an explicit extraction selector exists before evaluating it."""
        try:
            if await page.locator(selector).count():
                return
        except Exception as exc:
            raise await cls._selector_not_found_error(
                page=page,
                parameter_name="extract_selector",
                selector=selector,
            ) from exc
        raise await cls._selector_not_found_error(
            page=page,
            parameter_name="extract_selector",
            selector=selector,
        )

    @classmethod
    async def _candidate_root_selectors(cls, page: Any) -> list[str]:
        """Return simple candidate root selectors present on the page."""
        candidates: list[str] = []
        for selector in cls._ROOT_SELECTORS:
            if await page.locator(selector).count():
                candidates.append(selector)
        return candidates or ["body"]

    @classmethod
    async def _selector_not_found_error(
        cls,
        *,
        page: Any,
        parameter_name: str,
        selector: str,
    ) -> RuntimeError:
        """Build a consistent selector guidance error for missing selectors."""
        candidates = await cls._candidate_root_selectors(page)
        candidate_text = ", ".join(candidates) if candidates else "body"
        return RuntimeError(
            "result_type: selector_not_found\n"
            f"Requested {parameter_name} '{selector}' was not found. "
            f"Retry without selectors or use one of: {candidate_text}."
        )

    @staticmethod
    def _describe_element_selector(
        tag_name: str, element_id: str, class_name: str
    ) -> str:
        """Build a human-readable selector label for logging and output."""
        if element_id:
            return f"{tag_name}#{element_id}"
        classes = [item for item in class_name.split() if item]
        if classes:
            return f"{tag_name}.{'.'.join(classes[:3])}"
        return tag_name

    @classmethod
    def _validate_wait_until(cls, wait_until: str) -> str:
        """Validate the requested Playwright navigation readiness state."""
        normalized = (wait_until or "").strip().lower() or "domcontentloaded"
        if normalized not in cls._VALID_WAIT_UNTIL:
            allowed = ", ".join(sorted(cls._VALID_WAIT_UNTIL))
            raise ValueError(
                f"result_type: invalid_request\nwait_until must be one of: {allowed}"
            )
        return normalized

    @staticmethod
    def _validate_url(url: str) -> None:
        """Reject non-web schemes and local/private network targets."""
        normalized = (url or "").strip()
        parsed = urlsplit(normalized)
        if parsed.scheme == "data":
            return
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                "result_type: blocked\nOnly http, https, and data URLs are supported"
            )
        try:
            resolve_public_url(normalized)
        except WebUrlPolicyError as exc:
            raise ValueError(f"result_type: blocked\n{exc}") from exc

    @staticmethod
    def _format_error(message: str) -> str:
        """Render browser failures with a compact machine-readable result type."""
        if message.startswith("result_type:"):
            return f"Browser error\n{message}"
        return f"Browser error\nresult_type: error\n{message}"

    @staticmethod
    def _extract_result_type(message: str) -> str:
        """Parse a `result_type:` prefix from an error message for structured logs."""
        first_line = (message or "").splitlines()[0].strip()
        if first_line.startswith("result_type:"):
            return first_line.split(":", 1)[1].strip() or "error"
        return "error"

    @classmethod
    def get_recovery_policy(cls) -> ToolRecoveryPolicy:
        return ToolRecoveryPolicy.REPLAY_SAFE
