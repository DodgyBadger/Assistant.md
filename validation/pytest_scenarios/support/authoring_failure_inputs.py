"""Authored workflows exercise caught, explicit, and uncaught tool failure channels."""

CAUGHT_PROBE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Catch one expected direct-tool failure
---

```python
try:
    await file_read(operation="unknown")
except RuntimeError as exc:
    await finish(status="skipped", reason=str(exc))

await finish(status="failed", reason="expected tool error was not raised")
```
"""


EXPLICIT_FAILURE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Report one intentional domain failure
---

```python
await finish(status="failed", reason="intentional validation failure")
```
"""


SESSION_SEARCH_FAILURE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Propagate one mandatory session discovery failure
---

```python
await session_ops(operation="search_sessions", query="private search query")
```
"""
