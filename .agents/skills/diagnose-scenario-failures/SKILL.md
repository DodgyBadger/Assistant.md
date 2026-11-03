---
name: diagnose-scenario-failures
description: Diagnose pytest scenario failures using phase reports, semantic observations and retained artifacts, including application startup errors, cleanup failures and worker loss.
---

Read pytest's verdict and the plugin's JSON/Markdown evidence together. Identify the earliest relevant setup, call or teardown failure, selected node, worker, exception and artifact directory. Preserve concurrent assertion and cleanup failures rather than treating one as the explanation for both.

Identify the behavioral consequence of the failing assertion. If it only freezes incidental UI wording, styling, layout or documentation/skill prose, update or remove that assertion and retain any meaningful behavioral coverage. Such failures do not establish a product defect or require restoring the old presentation. Exact output checks need an identified functional contract, such as machine-consumed output. The contract-preservation rules below apply to meaningful behavior.

Reproduce the smallest relevant case with isolated state and the same inputs. Keep protected mode when investigating worker behavior; use explicit in-process debugging only when needed and record the changed protection. Diagnose infrastructure failures separately from application behavior failures. Never weaken the expected behavior or substitute simulated in-app results to obtain green output. An explicitly documented external-service double may isolate environmental faults, but record the changed boundary and do not present it as live-service verification.

Use checkpoints, correlation fields and application outputs to locate the last established state transition. Redact registered values in shared excerpts; raw application files may require separate handling. A process death has no Python exception object: distinguish genuine fatal diagnostics from missing evidence and mark cleanup unconfirmed unless established independently.

Completed pytest teardown establishes that finalizers returned; establish application cleanup separately through resource or lifecycle observations. After a fault, verify a subsequent real application lifecycle when contamination or worker replacement matters. A deliberate failing probe should still exit nonzero; report recovery verification separately. Summarize the demonstrated cause, reproducer, evidence, fix verification and unresolved limits. Do not promise hard-hang or descendant-process cleanup without tested support.
