# Live Session Memory Evaluation Corpus

This directory contains privacy-safe, deterministic fixtures for evaluating the live session map, change classifier, and recovery-card behavior independently. The fixtures are not model transcripts and do not define product prose. They define canonical source events and semantic expectations that live or mocked model outputs can be scored against.

## Corpus Contract

`representative_sessions.json` contains ordered canonical messages, uncommitted failed-attempt metadata, three proposed compaction checkpoints per session, expected classifier dimensions for bounded deltas, and the expected final session-map state. Sequence indexes are contiguous within a session and every map source reference points to a canonical message.

The three output families must remain independently labelled:

- `expected_final_map` grades durable current-session state. It includes active and superseded state where the latter is required to explain a correction or goal transition.
- `classifier_batches` grades which map dimensions changed within an exact canonical range. It does not prescribe whether policy must immediately run generative reconciliation.
- `compaction_checkpoints.expected_recovery_card` grades immediate continuation state at that point. It intentionally excludes facts that belong in the durable map but are not needed for the next action.

`failed_attempts` are not canonical messages. They let evaluation verify that an uncommitted failure does not independently become map evidence or advance a canonical source watermark.

`change_detection_v2.json` is a separate frozen corpus for the broad reconciliation-gating experiment. Its two calibration cases and two untouched holdout cases each compare a compact current map with five canonical deltas, including true no-change batches. Each batch labels whether any reconciliation is needed and whether a miss would be consequential. Once a live holdout run occurs, its cases must not be relabelled or used to retune the v2 threshold.

## Predeclared Metrics

### Session Map

- Required-entry recall: fraction of expected entries whose dimension, lifecycle status, meaning, and required source references are present.
- Unsupported-entry rate: fraction of produced entries whose asserted meaning has no admissible canonical source.
- Stale-active rate: fraction of superseded, resolved, cancelled, or explicitly waived state still represented as active.
- Unchanged-entry mutation rate: fraction of entries outside a labelled dirty dimension whose stable identity or meaning changes during an incremental update.
- Source-reference precision: fraction of emitted source references that exist, fall within the authoring range, and actually support the entry.

### Change Classifier

- Per-dimension precision and recall over `attention`, `goals`, `work_items`, `decisions`, `constraints`, `commitments`, `open_questions`, `artifacts`, and `observations`.
- Consequential false-negative rate, reported separately for corrections, decisions, constraints, commitments, and open questions.
- Calibration data: retain every raw probability with the expected label, resolved model version, prompt-contract version, latency, and usage so thresholds can be recomputed without rerunning the service.

### Recovery Card

- Continuation coverage: fraction of checkpoint `required_concepts` represented by the card, allowing semantic grading rather than exact prose matching.
- Historical leakage: fraction of `forbidden_concepts` incorrectly carried into the card as current state.
- Unsupported-claim rate: fraction of operational claims that are not supported by the map revision or bounded canonical tail supplied to the card author.
- Prompt size: input and output characters plus tokenizer-estimated tokens, recorded separately for every compaction round.
- Repeated-card drift: change in continuation coverage, historical leakage, unsupported-claim rate, and active-state correctness from the first to the third compaction.

### Continuation

- Active-goal accuracy, current-focus accuracy, blocker accuracy, next-action accuracy, and volatile-artifact accuracy are scored separately so a fluent but operationally incomplete answer cannot receive a passing aggregate score.
- A result must abstain when the fixture leaves an open question unresolved. Inventing closure counts as both an open-question miss and an unsupported claim.

## Baseline Procedure

The deterministic integration scenario validates corpus integrity, replayability, and the current recursive recovery-card source contract without calling a model. The live `experiments/repeated_compaction_live_probe` scenario consumes the same cases and records current-model cards, captured prompt sizes, literal diagnostic indicators, and blank semantic-review fields. Its prose is never an integration assertion or merge gate. Set `ASSISTANTMD_VALIDATION_OPENAI_API_KEY` when intentionally running the probe; it writes the value only to the scenario's isolated encrypted secret store and never to its artifacts. Baseline and future variants must use the same frozen case IDs, canonical messages, checkpoints, and expectations; calibration cases must not be silently rewritten after model results are observed.

Acceptance thresholds for classifier promotion and map authoring are intentionally set in their later experimental slices before those labelled runs. Slice 0 freezes what is measured and what the correct state means; it does not manufacture model-quality evidence from deterministic stub prose.

`subagent_proxy_baseline.json` records a temporary nine-card proxy benchmark produced by isolated Codex subagents under the current `recovery-card-v3` contract. It is useful for checking the rubric and exposing obvious fixture gaps, but it is not a measurement of AssistantMD's configured runtime model, latency, usage, or provider behavior.
