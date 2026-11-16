"""One owned page surface; root and sign-in pages have independent identities."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from playwright.async_api import Frame, Page

from .observation import PageObservation
from .viewer import HumanViewer


@dataclass(eq=False)
class BrowserSurface:
    page: Page
    page_id: str = field(default_factory=lambda: uuid4().hex)
    document_revision: int = 0
    observation: PageObservation | None = None
    viewer: HumanViewer | None = None

    async def prepare(self, browser_id: str) -> None:
        self.page.set_default_timeout(5000)
        for event in ("framenavigated", "frameattached", "framedetached"):
            self.page.on(event, self._document_changed)
        connection = await self.page.context.new_cdp_session(self.page)
        self.observation = PageObservation(connection, browser_id, self.page_id)
        self.viewer = HumanViewer(
            await self.page.context.new_cdp_session(self.page),
            self.page_id,
            lambda: self.document_revision,
        )

    def _document_changed(self, _frame: Frame) -> None:
        self.document_revision += 1

    def invalidate(self) -> None:
        if self.viewer is not None:
            self.viewer.invalidate()

    async def dispose(self) -> None:
        try:
            if self.observation is not None:
                await self.observation.close()
        finally:
            if self.viewer is not None:
                await self.viewer.close()
