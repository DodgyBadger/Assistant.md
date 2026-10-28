"""Authored pending-file journeys and explicit historical-mtime input edits."""

from __future__ import annotations

import os
from pathlib import Path


def append_with_mtime(path: Path, text: str, *, mtime: float | None) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    os.utime(path, None if mtime is None else (mtime, mtime))


PENDING_DIFF_WORKFLOW = """---
run_type: workflow
enabled: false
description: Pending diff metadata validation
---

## Run

```python
listed = await file_read(operation="list", path="meetings")
pending = await pending_files(operation="get", items=listed)
if pending.items:
    first = pending.items[0]
    diff = first.metadata.get("pending_diff", {})
    if diff.get("available"):
        await file_write(
            operation="write",
            path="outputs/pending-diff.md",
            content=(
                f"available={diff.get('available')}\\n"
                f"snapshot_set_id={diff.get('snapshot_set_id')}\\n"
                f"file_snapshot_id={diff.get('file_snapshot_id')}\\n"
                + diff.get("text", "")
            ),
        )
    await pending_files(operation="complete", items=pending.items)
await finish(status="completed", reason="pending-diff-done")
```
"""


PENDING_HYBRID_WORKFLOW = """---
run_type: workflow
enabled: false
description: Pending hybrid validation
---

## Run

```python
listed = await file_read(operation="list", path="tasks")
pending = await pending_files(operation="get", items=listed)
if pending.items:
    await pending_files(operation="complete", items=pending.items)
await finish(status="completed", reason="pending-hybrid-done")
```
"""
