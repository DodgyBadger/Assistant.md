"""Real rendered UI observation without exporting private pixels or input."""

from contextlib import asynccontextmanager
from urllib.parse import urlsplit


@asynccontextmanager
async def private_browser_page(application, browser, *, forbidden=()):
    # Deliberately bypass ordinary observed_page and its screenshots/traces.
    # This context is owned here, never by a diagnostic page fixture. Requested
    # URLs are checked against the app origin; real responses pass through.
    # Route interception alone is not a redirect-safe network sandbox.
    context = await browser.new_context(
        viewport={"width": 1440, "height": 1000}, service_workers="block"
    )
    page = await context.new_page()
    origin = urlsplit(application.url)
    counts = {"page_errors": 0, "denied_requests": 0, "private_console_leaked": False}

    def page_error(error):
        counts["page_errors"] += 1
        counts["private_console_leaked"] |= any(
            value in str(error) for value in forbidden
        )

    def console(message):
        # Inspect actual console output in memory; retain only the verdict.
        counts["private_console_leaked"] |= any(
            value in message.text for value in forbidden
        )

    async def admit(route):
        target = urlsplit(route.request.url)
        if (target.scheme, target.hostname, target.port) == (
            origin.scheme,
            origin.hostname,
            origin.port,
        ):
            await route.continue_()
        else:
            counts["denied_requests"] += 1
            await route.abort("blockedbyclient")

    async def deny_socket(route):
        counts["denied_requests"] += 1
        await route.close(code=1008)

    page.on("pageerror", page_error)
    page.on("console", console)
    await context.route("**/*", admit)
    await context.route_web_socket("**/*", deny_socket)
    try:
        yield page
    finally:
        await context.close()
        closed = context not in browser.contexts
        application.app.evidence.save_json(
            "private-browser-cleanup.json", {**counts, "context_closed": closed}
        )
        assert closed, "Private UI context cleanup is unconfirmed"
        assert counts["page_errors"] == 0, "Private UI produced a JavaScript error"
        assert not counts[
            "private_console_leaked"
        ], "Private input reached browser console output"
