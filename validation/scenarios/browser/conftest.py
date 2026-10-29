"""App-owned browser wiring; official Playwright fixtures own browser creation."""

import os

import pytest
import pytest_asyncio
from playwright._impl._driver import compute_driver_executable

from validation.scenarios.application import _ambient_credential, app
from validation.scenarios.support.browser_runtime import (
    BrowserApplication,
    app_http_listener,
    observed_page,
)
from validation.scenarios.support.vault_inputs import document_pdf
from validation.support.core_processes import declare_core_command

__all__ = ["app"]

collect_ignore = ["experiments"]


@pytest.fixture(scope="session", autouse=True)
def browser_driver_admission():
    # Playwright exposes no public driver command fixture. This installed-version
    # boundary identifies its real Node driver; execution is never substituted.
    # Chromium is launched by that driver and remains outside Python audit hooks.
    original = {
        name: value for name, value in os.environ.items() if _ambient_credential(name)
    }
    for name in original:
        os.environ.pop(name, None)
    executable, script = compute_driver_executable()
    declare_core_command([str(executable), str(script), "run-driver"])
    try:
        yield
    finally:
        os.environ.update(original)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "service_workers": "block",
        "viewport": {"width": 1440, "height": 1000},
    }


@pytest_asyncio.fixture
async def browser_app(app, request):
    vault = app.vault("BrowserVault")
    app.write(vault, "Welcome.md", "Browser pilot workspace")
    (vault / "source.pdf").write_bytes(
        document_pdf("Browser imported document content.")
    )
    await app.start()
    try:
        async with app_http_listener(app) as url:
            yield BrowserApplication(app, url, vault)
    except BaseException:
        request.session.shouldfail = (
            "Browser app listener ownership is unconfirmed; start a fresh run"
        )
        raise


@pytest_asyncio.fixture
async def ui(browser_app, page, browser, request):
    try:
        async with observed_page(browser_app, page, browser) as observed:
            yield observed
    finally:
        if page.context in browser.contexts:
            request.session.shouldfail = (
                "Browser context cleanup is unconfirmed; start a fresh run"
            )
