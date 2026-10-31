"""Bounded observation creates real filesystem contention, never replaces storage."""

import sys
import threading
from contextlib import contextmanager


@contextmanager
def block_first_binary_parent(vault, *, preserve_user_edit=False):
    from core.ingestion.storage import write_vault_file_bytes

    if sys.getprofile() is not None or threading.getprofile() is not None:
        raise RuntimeError("Cannot replace an existing execution observer")
    observed = []

    def observe(frame, event, arg):
        if (
            event != "call"
            or frame.f_code is not write_vault_file_bytes.__code__
            or observed
        ):
            return
        # Observe an actual public mutation entry after the Markdown write.
        # The fixture creates external filesystem contention; the original
        # binary writer, compensation, hash checks and logger execute unchanged.
        observed.append(True)
        markdown = vault / "local-fallback.md"
        assert markdown.exists()
        if preserve_user_edit:
            markdown.write_text("USER_MODIFIED_IMPORT")
        parent = vault / "assets/local-fallback"
        parent.mkdir(parents=True, exist_ok=True)
        assert not list(parent.iterdir())
        parent.rmdir()
        parent.write_text("EXTERNAL_PATH_CONFLICT")

    threading.setprofile_all_threads(observe)
    try:
        yield observed
    finally:
        threading.setprofile_all_threads(None)
        sys.setprofile(None)
