---
schedule: every 1d at 9am
workflow_engine: step
enabled: true
description: Malformed workflow with invalid legacy schedule syntax
---

## STEP1
@output file: test.md

This workflow has invalid schedule syntax (old format) and should fail to load without crashing the system.
