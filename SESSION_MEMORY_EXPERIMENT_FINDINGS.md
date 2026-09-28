# Session Memory Experiments: Findings and Implications

## Summary

These experiments began with a practical concern: repeated conversation compaction can gradually distort a long-running session. The obvious description of this problem is factual loss, but the experiments suggest that the more important failure mode is often subtler. Core facts usually survive. What changes over successive summaries is emphasis, interpretation, and the narrative connecting decisions—the conversational equivalent of a game of telephone.

The strongest conclusion is that durable conversational memory is not primarily about retaining more text. It is about maintaining a compact and current interpretation while preserving a trustworthy path back to the original evidence.

Two durable mechanisms emerged:

- A compact representation, such as a recovery card or structured session map, keeps the model oriented.
- A bounded transcript-search tool lets the model verify exact facts, decisions, and provenance without replaying the entire conversation.

Transcript retrieval is already a meaningful improvement independent of whether structured session maps ultimately replace recovery cards. Session maps are promising but not yet proven superior over the full lifetime of a very long conversation. Jev demonstrated movement detection and occasionally improved tight-budget ranking, but neither use produced enough value to justify another memory subsystem or production dependency. Jev memory work is closed for this branch.

## What We Tested

We first examined existing long conversations and their successive recovery cards, looking beyond obvious factual contradictions to changes in salience, interpretation, and overall narrative. We then added bounded lexical search and window retrieval over the canonical in-session transcript and taught recovery cards to cue the agent to retrieve original evidence when summarized state was insufficient.

Next, we compared recovery-card compaction with a structured session map updated as older messages left the active context. The map represented goals, constraints, decisions, artifacts, open questions, and next actions, with claims linked to canonical transcript message ranges. We replayed two long, real-world conversations through five successive reduction boundaries under three conditions: recovery cards, unconditional map rewriting, and map rewriting gated by Jev, an inexpensive decision model.

Finally, we ran a small labelled classifier probe covering repetition, minor clarification, transient brainstorming, unaccepted proposals, explicit completion, user adoption, constraint reversal, and durable artifact changes. These tests were designed to reveal useful behavior and failure modes, not to establish a universal benchmark.

## Findings

### 1. Recovery cards work better than a simple failure narrative suggests

The experiments did not show recovery cards routinely losing central facts or making long conversations unusable. They generally maintained goals, constraints, decisions, and immediate next actions well. The retained recent-message tail also compensated for some weaknesses in the compacted representation.

The concern remains legitimate, but it should be stated precisely: repeated rewriting can slowly alter which details appear important, how earlier events are interpreted, and which narrative becomes dominant. This semantic and salience drift is harder to detect than a wrong date or missing filename and is unlikely to be captured by ordinary summary-similarity metrics.

### 2. Transcript retrieval is a strong independent improvement

A compact summary cannot simultaneously remain small, preserve every nuance, and retain exact provenance. Bounded transcript search provides an escape hatch: the compact representation can orient the agent, while the original transcript remains available for verification.

In the controlled retrieval probe, all three memory conditions correctly recovered the final technical resolution, the resulting request path, and the earlier hypotheses displaced by later evidence. They did so through search and bounded transcript windows rather than by replaying the full history.

This makes transcript retrieval worth retaining even if rolling eviction or structured maps are abandoned. It directly addresses the provenance problem and reduces the consequences of imperfect summarization.

### 3. The retained verbatim tail masks compact-memory defects

Recent raw messages do substantial corrective work. During testing, a map could be stale or incomplete on its own while the complete effective context still led the agent to the correct interpretation because newer messages remained verbatim.

This means memory evaluations should separately inspect:

- The compact artifact by itself.
- The compact artifact plus the retained verbatim tail.
- The agent’s eventual answer when transcript retrieval is available.

Scoring only final answers can make a defective compact representation look healthy. Scoring only the compact representation can understate the quality of the actual system presented to the model.

### 4. Structured maps were materially smaller and made current state clearer

Across the controlled replays, structured maps were substantially smaller on average than recovery cards while preserving the principal goals, constraints, decisions, artifacts, open questions, and superseded directions. In one replay the map artifact was roughly half the average size of the recovery card; the second produced a similar directional result.

The map format was particularly useful for distinguishing current state from replaced state. It encouraged obsolete branches to be revised or removed rather than repeatedly narrated. Recovery cards preserved more commands, intermediate analysis, and narrative texture, but that richness also increased context size and allowed stale interpretations more places to survive.

Smaller is not automatically better, however. In one stochastic retrieval comparison, the smaller maps led the agent to perform more searches and retrieve more windows than the richer recovery card. Compact-memory size can shift cost into retrieval rather than eliminate it. The meaningful measures are total model work, latency, cost, and grounded answer quality.

### 5. Semantic retention looks competitive, but superiority is not established

The maps retained the frozen semantic checklists across two different conversation types and supported correct evidence recovery. They did not exhibit a clear factual or salience collapse across five successive checkpoints. This is enough to treat rolling eviction as a credible strategy.

It is not enough to conclude that maps solve long-term semantic drift better than recovery cards. Five checkpoints across two conversations do not reproduce the accumulated transformations of a months-long session. Maps may also underrepresent narrative texture or emerging ideas that have not yet crystallized into an explicit decision.

The evidence therefore supports continued opt-in live evaluation, not a default cutover.

### 6. Provenance must be an enforceable data contract

One experiment gave the map author access to recent retained messages as contextual lookahead while instructing it not to cite them. The model used information from those messages but attached citations to older messages that did not support the resulting claim. Every cited message existed, yet the citations did not entail the claim.

This was provenance laundering, and stronger prompt wording was not an adequate remedy. The corrected design made every message visible to the author citable and subjected retained and evicted evidence to the same validation rules.

Two distinct checks are therefore necessary:

- Citation resolution: does the referenced source exist and belong to the conversation?
- Citation entailment: does that source actually support the claim being made?

Mechanical reference validation alone cannot establish grounded memory.

### 7. Assistant proposals should not silently become user decisions

Long conversations contain plans, suggestions, rejected branches, hypothetical examples, and tool-observed facts alongside explicit user decisions. A conventional summary can flatten these distinctions and accidentally promote an assistant suggestion into accepted session state.

Tracking evidence basis proved useful. The map could preserve a potentially relevant assistant proposal without claiming that the user had adopted it. This conservative behavior occasionally made the map appear less decisive, but it reduced a meaningful class of unsupported claims.

The important distinction is not merely who said something. It is whether the item was proposed, accepted, directly stated, or observed through a tool or artifact.

### 8. Temporal correctness needs better evaluation categories than summary similarity

The most useful scoring categories were:

- Accurate current state.
- Historical state clearly marked as superseded.
- Stale state incorrectly presented as current.
- Omitted information.
- Unsupported promotion of a suggestion or inference into established state.

These categories capture the actual risks of long-running conversational memory. Generic similarity scores or factual checklists cannot reliably distinguish a useful historical record from a dangerously stale instruction.

### 9. A cheap classifier was safest as a cost optimization, not an information filter

Jev was used to estimate whether accumulated new evidence represented enough movement to justify rewriting the session map. When it deferred a rewrite, the evidence was preserved and included again on the next classification attempt. The classifier could delay interpretation, but it could not discard source material.

This cumulative behavior is essential. Several individually minor clarifications may collectively produce a meaningful change. A classifier that sees only the newest slice can defer each small change forever and lose their aggregate significance.

The classifier should therefore answer a narrow question: has the current map diverged enough from all evidence accumulated since the last authored map to justify paying for another rewrite? It should not decide which evidence is permanently important, assign detailed semantic categories, or become the authority of record.

### 10. Classifier economics depend on slice size

In a change-dense replay with relatively large batches, every interval contained a material development and Jev saved no map-authoring calls. The classifications were reasonable but economically redundant.

In the second replay, Jev defensibly deferred one rewrite, carried the evidence forward, and triggered on a later cumulative change. In the labelled small-slice probe, a provisional threshold of 0.5 cleanly separated 24 trials: repetition, minor clarification, transient brainstorming, and unaccepted proposals stayed below the threshold, while completion, adoption, reversals, and artifact changes stayed above it.

Because the score represents evidence of material movement, a lower threshold causes more rewrites and a higher threshold causes more deferrals. There is no universally correct threshold independent of prompt, model, slice size, and acceptable risk.

The likely value of a cheap gate lies in frequent checks over small slices, where many intervals contain no durable movement. Applied only to large, eventful batches, it is likely to add cost without avoiding much generative work.

### 11. Search and structured memory solve different problems

The map provides orientation: what is current, what changed, what remains unresolved, and where the work is headed. Transcript retrieval provides verification: what exactly was said, why a decision was made, and which evidence supports a claim.

Neither eliminates the other. A perfect retrieval tool does not tell the agent what it should investigate without some orientation. A perfect-looking map should not be trusted as the sole source for exact or disputed historical claims. Their combination is more robust than treating either summarization or retrieval as a complete memory system.

### 12. A low retained-context target is workable, but turn boundaries dominate the exact size

A follow-up replay tested 20,000-token and 40,000-token low-watermark targets with the 150,000-token high watermark unchanged. A clean 360-message suffix of the 1065 redevelopment conversation produced three successive map rewrites per condition. The 20,000-token condition ended at approximately 62,700, 43,900, and 17,300 effective tokens; the 40,000-token condition ended at approximately 62,600, 43,800, and 26,200. A second clean conversation produced approximately 25,400 and 25,700 effective tokens for the two targets.

These results do not mean the planner ignored its setting. Eviction preserves complete provider-history groups, and a single tool-heavy turn can exceed either target. The low watermark is therefore a selection target rather than a hard postcondition. In four of the paired checkpoints, both settings selected the same canonical boundary; their independently generated maps differed anyway, showing that stochastic authoring currently creates more variation than the watermark when the source partition is identical.

The smallest resulting context retained the active compensation offer, agreement-closing issues, consultant arrangement, side-letter constraints, future-rights question, and source provenance while removing stale intermediate state. No clear semantic or salience failure appeared across the three rewrites. This is enough to make 20,000 tokens the preferred opt-in tuning value for further live evaluation: it captures the token benefit when conversational group sizes permit it, while whole-group preservation automatically retains more context when they do not. It is not evidence that 20,000 is a universal optimum or that the map strategy should become the default.

The replay also exposed a separate correctness edge case. Real transcripts can contain an abandoned user turn with no assistant response before a later completed turn. The planner initially treated such an incomplete historical group as a permanent non-evictable prefix, so an otherwise eligible long session returned `incomplete_eviction_prefix`. The corrected planner now treats the later user turn as the boundary that makes the abandoned input historical canonical evidence: it can leave effective context without being deleted or omitted, while the newest active turn remains non-evictable and malformed tool history remains blocked.

### 13. A small narrative bridge is a practical hybrid

The sharpest difference between recovery cards and structured maps was narrative freedom rather than factual capacity. A hybrid Compaction v2 checkpoint therefore added one source-linked trajectory alongside the typed current-state entries, authored and validated in the same model call rather than maintaining a second summary. Across twelve live checkpoints, trajectory text remained between 369 and 551 characters while preserving the causal transitions most likely to be flattened by a field-only representation: why the budget and shortlist changed, why outreach paused and resumed, and why site selection remained open.

All twelve authoring tasks completed, including retrieval-heavy retrospective turns, and the final effective context remained approximately 2,050 estimated tokens. The trajectories were not miniature transcripts and did not simply repeat every entry. This is positive evidence that a small narrative bridge can recover some recovery-card expressiveness without giving up structure, provenance, or compactness. Because the live user simulator produced a different conversation and checkpoint cadence from the prior map-only run, it is not a controlled demonstration that the hybrid is smaller or more accurate.

## Current Interpretation

The experiments support three decisions with different confidence levels.

First, bounded in-session transcript retrieval and an explicit cue to use it are durable improvements and should remain regardless of later memory choices.

Second, rolling eviction backed by a provenance-aware hybrid checkpoint is promising enough for continued opt-in use. Its bounded trajectory preserves causal orientation while typed entries represent current state and supersession; canonical sources preserve auditability and retrieval. It has not yet earned replacement of recovery-card compaction as the default.

Third, Jev demonstrated credible movement classification but not enough value as a map-author gate. The gate changes when an inevitable rewrite occurs rather than eliminating the underlying work, and its savings depend on slice cadence while adding another runtime mode and failure path. The map should therefore use its configurable eviction watermarks directly and author unconditionally at each selected boundary. Gate-specific code should be removed while the generic decision-model capability remains available for better-supported uses.

Retrieval-context admission was the next hypothesis: after the agent explicitly requests search, a cheap classifier might rerank a high-recall candidate pool before a deterministic token budget admits raw evidence into frontier-model context. The broader evaluation did not improve complete-answer coverage and showed that candidate recall, diversity, and deterministic budget allocation dominate the result. That line is also closed for this branch; the measurements remain documented in [Retrieval Context Admission: A Role for Cheap Decision Models](RETRIEVAL_CONTEXT_ADMISSION_DESIGN.md).

## Open Questions

- Does a structured map resist salience and narrative drift over dozens of successive updates, rather than five?
- How much narrative texture can be removed before orientation degrades or retrieval becomes excessively frequent?
- How often do unusually large provider-history groups prevent the configured low watermark from producing the intended context savings?
- What is the total cost of smaller maps once additional retrieval calls are included?
- How reliably does the agent recognize when compact memory is insufficient and transcript verification is warranted?
- Can citation entailment be evaluated cheaply enough to complement mechanical provenance validation?
- When, if ever, is the evidence strong enough to replace recovery-card compaction rather than retaining both strategies?

## Closing Observation

The original question was whether a live session map could prevent the telephone effect of repeated compaction. The experiments produced a more useful framing: no compact representation should be expected to preserve every nuance indefinitely. A durable memory system should instead keep current state concise, make supersession explicit, distinguish evidence from proposal, and preserve a bounded route back to the canonical conversation whenever accuracy matters.
