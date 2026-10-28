"""Retired operation workflow input retained for real execution rejection."""

RETIRED_OPERATION_WORKFLOW = """---
run_type: workflow
enabled: false
description: Validate explicit rejection of a retired summary operation
---

```python
await session_ops(operation="summarize_session")
```
"""
