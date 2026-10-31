"""Real workflow and context inputs for application scenarios."""

PROJECT_WORKFLOW = '---\nrun_type: workflow\nenabled: false\ndescription: Project-local research workflow\n---\n```python\nawait file_write(\n    operation="write",\n    path="Research/Forest/library-processed.md",\n    content="processed\\n",\n)\nawait finish(status="completed", reason="project-workflow-complete")\n```\n'

CONTEXT_TEMPLATE = '---\nrun_type: context\ndescription: Context template that must not run by path\n---\n```python\nawait assemble_context(instructions=["not executable as a workflow"])\n```\n'

ORDINARY_MARKDOWN = '# Example\n\nThis note contains Python documentation but is not a workflow.\n\n```python\nprint("example")\n```\n'

FAILING_WORKFLOW = '---\nrun_type: workflow\ndescription: Project-local workflow that fails deterministically\n---\n```python\nraise RuntimeError("expected project workflow failure")\n```\n'

ASYNC_PROBE_WORKFLOW = '---\nrun_type: workflow\nenabled: false\ndescription: Async workflow_run probe\n---\n```python\nawait file_write(\n    operation="write",\n    path="notes/workflow-run-async-write.md",\n    content="created before cancellation\\n",\n)\nawait job(operation="wait", timeout_seconds=120)\n```\n'

SCHEDULED_CONTROL_WORKFLOW = """---
run_type: workflow
enabled: true
schedule: cron: 0 0 1 1 *
---
```python
await finish(status="completed", reason="scheduled-control")
```
"""
