# Retrieval Context Admission: A Role for Cheap Decision Models

## Status

Closed for this branch. Two disposable evaluations are complete. Comparative Jev reranking can move a directly useful excerpt ahead of lexical noise under a tight context budget, but the broader natural-query evaluation did not improve complete-answer coverage and exposed candidate-generation, multi-facet coverage, and budget-allocation failures. The evidence does not support production activation or further Jev memory experiments on this branch. This document preserves the result; Compaction v2 and transcript retrieval have no Jev dependency. Generic decision-model infrastructure remains available for separately justified features.

## Problem

Agent memory eventually becomes a context-allocation problem. Once older session messages have left active context, searching them means querying a body of evidence already known to exceed the model’s working budget. The same pressure appears more strongly across sessions, vault documents, cached tool output, and web research.

Retrieval systems should search broadly enough to avoid missing the answer, but a high-recall candidate set can itself be too large, redundant, or weakly related to place in the frontier model’s context. Returning fewer candidates directly from lexical or semantic search reduces context use but risks excluding the decisive source before the model can inspect it.

The opportunity for a cheap classifier lies between retrieval and context admission. The frontier model remains responsible for deciding when to search and for interpreting evidence. The classifier helps decide which already-retrieved evidence receives expensive attention first.

## Proposed Boundary

The classifier is retrieval infrastructure, not a participant in the agent’s reasoning loop. It runs only after the agent or another explicit workflow has requested retrieval.

```text
Agent requests retrieval
        ↓
Retriever produces a high-recall candidate set
        ↓
Candidates remain in canonical storage or the off-context cache
        ↓
A bounded comparative decision call ranks the candidate group
        ↓
A deterministic token budget selects a diverse evidence tranche
        ↓
The agent receives raw excerpts and stable source handles
```

This boundary preserves the strengths of frontier models. The agent chooses whether retrieval is warranted, formulates the request, interprets conflicting sources, and decides whether to retrieve more. The classifier performs a narrow, repetitive admission task whose output can be measured independently.

## Initial Decision Contract

The first experiment compared two contracts. Independent per-candidate relevance and answer-support decisions lacked the neighboring evidence needed to reason well about chronology, supersession, and redundancy. A single comparative call over the bounded candidate group was faster, used fewer calls and fewer total input tokens, and produced better ordering. The next evaluation should therefore use one scalar admission-priority value per candidate within a comparative request.

- Does this candidate directly support answering the retrieval request rather than merely sharing terminology?
- Relative to the other supplied candidates, should this evidence receive scarce frontier-model context?

Later experiments may test additional signals only when a demonstrated failure justifies them:

- Whether the candidate is original evidence or a secondary paraphrase.
- Whether adjacent context is likely necessary to interpret it.
- Whether it contributes information not already present in selected candidates.
- Whether it records a later supersession or an earlier state.

The score is an ordinal ranking signal, not a calibrated probability or evidence that a claim is true. Temporal status, novelty, and coverage of distinct question facets remain group-selection problems. Adding more prose to the scalar prompt did not resolve the observed temporal miss, so additional output dimensions should be introduced only when a larger evaluation demonstrates that a deterministic selector can use them effectively.

## Context Selection

Classification scores do not directly determine truth or deletion. A deterministic selector applies the actual context budget and should account for more than score ordering.

The selector should:

- Preserve a measurable lexical or hybrid baseline and fail back to its ordering when classification is unavailable. The first evaluation found that naive top-result reservation and simple rank fusion did not reliably prevent the temporal miss, so a production safeguard requires its own evidence rather than assumption.
- Prefer evidence diversity across sources, time ranges, and candidate clusters rather than filling the budget with near-duplicates.
- Admit raw source excerpts rather than classifier-written summaries.
- Preserve stable handles that allow the agent to expand a window, inspect a complete document, or request the next tranche.
- Report how many candidates were considered and omitted so the bounded result is not mistaken for exhaustive evidence.
- Preserve monotonicity: increasing the admission budget must not remove evidence selected under a smaller budget.
- Prevent one long message, source, or lexical cluster from occupying the budget with overlapping chunks when distinct sources or question facets remain uncovered.

The complete candidate set remains available outside active context. A false negative delays inspection but does not erase evidence.

## Source and Cache Responsibilities

Canonical sources remain authoritative. Current-session and cross-session results resolve to canonical chat message ranges. Vault results resolve to vault files or existing derived indexes. Web and oversized tool results may use the shared off-context cache, with stable references back to the fetched or extracted artifact.

The cache holds material that is too large to inline; it does not become an authority or justify copying canonical data into a second truth store. Classification metadata is derived and replaceable. Reclassification, a different ranking strategy, or a stronger model must not require refetching or reconstructing the source when the underlying evidence is still available.

## Relationship to Observed Memory Failures

This direction responds to problems observed during the session-memory experiments:

- Smaller maps sometimes required more searches and transcript windows than richer recovery cards.
- Compact memory could identify the relevant subject without containing enough evidence to answer precisely.
- Later paraphrases could obscure the original source or its temporal relationship to a current claim.
- Citation resolution did not guarantee that the cited evidence entailed the claim.
- The amount of searchable material grows precisely because it no longer fits in active context.

Candidate reranking can prioritize original, directly supportive evidence under a fixed budget. A related but separate experiment could use the same decision runtime to flag claim-source pairs whose cited evidence does not support the generated memory claim. Neither use asks the classifier to author memory or decide when the agent should search.

## Why This Is a Better Fit Than a Session-Map Gate

The map gate only changed the timing of an authoring call that cumulative evidence would eventually force. In change-dense intervals it added classification without saving any generative work. A fixed high-watermark and low-watermark policy provides a simpler, predictable map-authoring cadence.

Retrieval admission addresses an unavoidable scaling boundary instead. High-recall retrieval routinely produces more candidate evidence than should enter expensive context, and the pressure increases across longer sessions, more sessions, larger vaults, cached research, and web sources. Even modest improvements in evidence recall per admitted token could compound across many agent tasks.

## Safety Boundaries

A retrieval classifier may rank, prioritize, or request expansion. It must not:

- Decide whether the agent is allowed to search.
- Permanently discard evidence.
- Rewrite source material before admission.
- Resolve truth between conflicting sources.
- Override vault, session, principal, or network authorization.
- Treat classification probability as evidence that a claim is true.

Low confidence should broaden admission or preserve baseline results rather than silently exclude candidates. Access control and token limits remain deterministic.

## First Evaluation

The first evaluation should reuse source-sensitive questions and canonical answer ranges from the completed session-memory replays. For each question, produce the same deliberately broad candidate pool and compare:

1. Existing lexical ordering.
2. Existing hybrid lexical and semantic ordering where available.
3. Cheap-classifier reranking over the same candidates.

Give every condition the same strict evidence-token budget. Measure:

- Recall of the canonical answer evidence within the admitted budget.
- Recall of the relevant supersession or contradiction event.
- Redundant tokens admitted.
- Number of follow-up expansions required for a grounded answer.
- Classification latency and cost.
- Variance across repeated classifications.

The primary question is whether classifier reranking preserves or improves canonical-evidence recall while materially reducing the retrieved text admitted to the frontier model. It is not whether the classifier’s scores sound intuitively reasonable.

Run this first as a disposable evaluation over existing retrieval outputs. Do not add classifier calls to production transcript, vault, cache, or web paths until the bounded comparison demonstrates a useful recall-per-token improvement over the current ranking baselines.

## Initial Evaluation Result

The disposable evaluation used two private real-world transcripts and ten preregistered, source-sensitive questions. FTS5 lexical search produced the same bounded pool of 16 distinct canonical message chunks for each condition. Long messages were divided into overlapping chunks of approximately 1,600 characters, and the selector admitted candidates in rank order under fixed estimated-token budgets. Gold labels identified one or more canonical message groups needed for a complete answer. The experiment measured complete group coverage and reciprocal rank rather than whether a score sounded plausible.

At a 400-token admission budget, lexical ordering completely covered 7 of 10 evidence sets and comparative Jev ordering covered 9 of 10. At 1,200 tokens, lexical ordering saturated at 10 of 10 while Jev covered 9 of 10 under the strict labels. The result supports reranking when evidence context is scarce; it does not show a benefit when the baseline can already admit enough of the candidate pool.

Comparative ranking was materially better than independent candidate classification. On the five-question authentication-proxy transcript, independent scoring required 80 calls, approximately 65,000 provider-reported input tokens, and 31 seconds of summed call latency while covering 4 of 5 questions at 1,200 admitted tokens. Comparative ranking required five calls, approximately 42,500 input tokens, and 1.5–2.0 seconds of summed call latency, improved first-evidence ordering, and covered the same 4 of 5 strict evidence sets. The held-out co-op intake transcript used five calls, approximately 32,800 input tokens, and 1.6 seconds of summed latency while preserving complete coverage on all five questions.

The rankings were repeatable enough for continued evaluation. Two live comparative runs preserved the same top three candidates for all ten questions, and per-question full-ranking Spearman correlations ranged from 0.88 to 1.00. The absolute scores varied and clustered in moderate ranges, reinforcing that they should be consumed only as relative ordering values.

The remaining miss concerned a question asking which earlier diagnosis was displaced by the eventual root cause. Jev consistently ranked the final root-cause messages first and found several genuinely relevant earlier hypotheses, but it did not prioritize the immediately preceding diagnosis selected by the strict gold label. A stronger prompt emphasizing distinct facets, before-and-after evidence, redundancy, and supersession increased provider-reported input by roughly 10–12 percent without correcting the miss. Reserving the top lexical result, interleaving rankings, and reciprocal-rank fusion also did not correct it under the same budget. This is evidence that relational coverage cannot be assumed to emerge from one scalar relevance order.

The evaluation has important limits. The retrieval queries were deliberately constructed to yield pools containing every labelled source, so it did not test lexical candidate-generation failure or vocabulary mismatch. The corpus contained only ten questions across two domains. Chunk sizes were large enough that individual candidates materially affected budget packing, especially below 400 tokens. The evaluation also did not measure answer generation, follow-up expansion count, redundant admitted tokens, or hybrid retrieval.

The next evaluation should run comparative reranking behind a strategy-neutral experimental ranking boundary over natural user queries and real lexical and hybrid candidate outputs. Use a bounded pool of roughly 12–16 source-linked chunks, normalize evidence units toward approximately 250–350 tokens, fill a deterministic admission budget, and fall back to the baseline order on decision failure. Include correction, supersession, conflicting-evidence, multi-facet, and vocabulary-mismatch cases. Measure complete canonical-evidence coverage per admitted token, redundant tokens, follow-up expansions, latency, cost, and repeated-run variance. Do not tune a score threshold: the useful output observed here was ordering under a budget.

## Natural-Query Evaluation Result

The broader disposable evaluation used five genuine retrospective requests from four real sessions. The cases covered confirming whether an earlier email had been shared, comparing three sources across a long negotiation, recovering a prior organizational purpose, auditing whether a delegate had actually run, and reproducing every driving-school link from a long research conversation. Historical candidates were split into source-linked chunks capped at 350 estimated tokens. The lexical condition used one or more short query variants, reserved the strongest result from each variant, and fused the remaining rankings into the same 16-candidate pool supplied to Jev. Both conditions used strict monotonic rank-prefix admission at 600- and 1,200-token budgets.

Candidate generation was the first limiting boundary. The candidate pool contained every labelled evidence group for only three of five cases. It recovered two of three source groups for the compensation comparison and eleven of twelve unique URLs for the exhaustive driving-school request. This was true even after query decomposition improved substantially over a single natural-language query. A reranker cannot repair those omissions.

On the three cases with complete pool recall, both lexical and Jev ordering achieved complete coverage at both budgets. Across all five cases, mean labelled-group coverage at 600 tokens was 0.67 for the lexical baseline and 0.77 in both Jev repeats. At 1,200 tokens it was 0.73 for lexical ordering and 0.83 and 0.77 for the two Jev repeats. Complete-case coverage remained three of five for every condition because neither reranking nor more admitted tokens could exceed the candidate pool's recall ceiling.

The apparent Jev gain came almost entirely from the exhaustive-links case. Jev consistently moved one compact source chunk containing six unique URLs to the first position, while the diversified lexical ordering admitted no labelled URL chunk within the same prefixes. It did not bring the other distinct link-bearing chunks into either budget, even though two of them were present in the pool. The compensation case showed the converse risk: at 1,200 tokens lexical ordering covered both available source groups, while Jev covered two groups in one repeat and only one in the other. A scalar relevance order can prioritize the strongest local answer while failing to preserve the separate sources needed for comparison or completeness.

Ten comparative calls consumed 60,440 provider-reported input tokens and 3,140 output tokens, with 3.09 seconds of summed provider latency. This remains operationally fast, but the input cost scales with the full candidate set. The two repeated rankings were stable on the simple cases and on the first driving-school chunk; they were not stable enough on the multi-source compensation case to treat ranking as a complete-answer guarantee.

A narrow follow-up asked Jev to select a complete evidence set directly under the stated token budget rather than assign scalar scores. It did not solve the coverage problem. Across both budgets and repeats, it selected only one driving-school chunk, covering six of twelve labelled URLs while using 206 tokens, and selected only chunks from the final compensation answer, covering one of three source groups while using 431–574 tokens. It left most of both budgets unused and sometimes chose multiple chunks from the same message. Jev therefore should not own budget allocation, deduplication, source diversity, or completeness detection on this evidence.

These results identify three distinct retrieval responsibilities:

1. Candidate generation must achieve high recall through query formulation, lexical or hybrid retrieval, and explicit expansion when the pool is incomplete.
2. A deterministic selector must enforce token limits, monotonicity, chunk and source deduplication, and diversity across query branches or evidence clusters.
3. Jev may provide an optional comparative relevance signal within that selector, particularly when lexical noise would otherwise consume a very tight first tranche, but it must not be the only mechanism protecting temporal, comparative, or exhaustive coverage.

The evaluation did not include a valid semantic candidate generator because canonical-message hybrid retrieval is not yet available. A disposable attempt first exposed and discarded the validation controller's deterministic embedding override; after restoring the live embedding alias, the isolated run stopped before processing content because the OpenAI embedding provider resolved an API-key placeholder rather than an OAuth credential. No result from either attempt is included in the measurements above. Hybrid recall remains the most useful next experiment once that credential path is available: improve the candidate pool first, compare lexical and hybrid recall using the same natural cases, and then measure whether Jev adds value over a deterministic diversified selector. Building a production classifier path before that comparison would optimize the wrong boundary.

## Potential Follow-Up: Claim-Source Audit

The most promising second use is a narrow entailment audit over generated memory claims and their cited source ranges. The decision question would distinguish direct support, partial support, contradiction, and absence of support. A questionable result would route the claim to a stronger reconciliation or regeneration pass; it would not silently approve or reject the claim.

This experiment directly targets the provenance-laundering failure observed when a map author used uncitable recent evidence but attached older citations. It should remain separate from reranking so the value and failure modes of each decision contract can be measured independently.

## Decision Gate

The initial probe passed the gate for a broader strategy-neutral evaluation; the broader evaluation does not pass the production-adoption gate. Jev improved evidence-group coverage in one noisy exhaustive case, but did not improve complete-answer coverage, could not repair candidate-generation misses, varied on a multi-source comparison, and performed poorly when asked to allocate the evidence budget directly. No Jev retrieval work should continue on this branch. Any future reconsideration should begin from a demonstrated retrieval failure and a strong lexical-or-hybrid deterministic baseline, not from the availability of the classifier.
