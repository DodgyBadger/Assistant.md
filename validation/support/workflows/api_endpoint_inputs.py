"""Workflow inputs for real API endpoint journeys."""

STATUS_PROBE_WORKFLOW = '---\nrun_type: workflow\nenabled: false\ndescription: Validation helper workflow\n---\n\n## Run\n\n```python\nawait finish(status="completed", reason="status-probe")\n```\n'

SKIPPED_PROBE_WORKFLOW = '---\nrun_type: workflow\nenabled: false\ndescription: Validation skipped workflow\n---\n\n## Run\n\n```python\nawait finish(status="skipped", reason="status-probe-skipped")\n```\n'

API_SYSTEM_PROBE_WORKFLOW = '---\nrun_type: workflow\nenabled: false\ndescription: API system workflow routing probe\n---\n\n## Run\n\n```python\nawait finish(status="completed", reason="api-system-probe")\n```\n'
