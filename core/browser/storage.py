"""Private per-driver temporary files outside persistent runtime roots."""

from __future__ import annotations

import asyncio
import shutil
import stat
from pathlib import Path
from tempfile import mkdtemp

from core.runtime.paths import get_data_root, get_system_root

from .operations import finish_cleanup


class BrowserTemporaryStorage:
    def __init__(self) -> None:
        self._path: Path | None = None
        self._identity: tuple[int, int] | None = None
        self._closing: asyncio.Task[None] | None = None
        self._removal: asyncio.Task[None] | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("Browser temporary storage is unavailable")
        return self._path

    def create(self) -> None:
        if self._path is not None:
            raise RuntimeError("Browser temporary storage is already allocated")
        parent = Path("/tmp").resolve(strict=True)
        roots = (get_data_root().resolve(), get_system_root().resolve())
        if any(parent.is_relative_to(root) for root in roots):
            raise RuntimeError(
                "Browser temporary storage overlaps persistent runtime state"
            )
        if not shutil.rmtree.avoids_symlink_attacks:
            raise RuntimeError(
                "Browser temporary cleanup requires safe directory removal"
            )
        path = Path(mkdtemp(prefix="assistantmd-browser-", dir=parent))
        try:
            if any(path.resolve(strict=True).is_relative_to(root) for root in roots):
                raise RuntimeError(
                    "Browser temporary storage overlaps persistent runtime state"
                )
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
                raise RuntimeError("Browser temporary storage permissions are unsafe")
        except BaseException:
            path.rmdir()
            raise
        self._path = path
        self._identity = (info.st_dev, info.st_ino)

    async def close(self) -> None:
        if self._path is None:
            return
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await finish_cleanup(self._closing)

    async def _close(self) -> None:
        self._removal = asyncio.create_task(asyncio.to_thread(self._remove))
        self._removal.add_done_callback(self._completed)
        _, pending = await asyncio.wait({self._removal}, timeout=5)
        if pending:
            raise RuntimeError("Browser temporary storage removal is unconfirmed")
        self._removal.result()
        self._path = None
        self._identity = None

    def _remove(self) -> None:
        path = self.path
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or (info.st_dev, info.st_ino) != self._identity
        ):
            raise RuntimeError(
                "Browser temporary storage identity changed; cleanup unconfirmed"
            )
        # Delete only the original owned path. Never resolve a replacement link.
        shutil.rmtree(path)

    @staticmethod
    def _completed(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()
