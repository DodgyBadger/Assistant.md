# Retrieval Context Admission: A Role for Cheap Decision Models

## Status

This document records a design direction for evaluation, not an approved production implementation. It replaces the session-map rewrite gate as the leading hypothesis for how a cheap, fast decision model could contribute to agent memory.

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
Cheap decision calls score candidates in parallel
        ↓
A deterministic token budget selects a diverse evidence tranche
        ↓
The agent receives raw excerpts and stable source handles
```

This boundary preserves the strengths of frontier models. The agent chooses whether retrieval is warranted, formulates the request, interprets conflicting sources, and decides whether to retrieve more. The classifier performs a narrow, repetitive admission task whose output can be measured independently.

## Initial Classification Questions

The first experiment should use the smallest useful decision contract:

- **Query relevance:** Does this candidate directly address the retrieval request rather than merely share terminology?
- **Answer support:** Does it contain evidence that could answer the request, or is it only commentary, navigation, or a later paraphrase?

Later experiments may test additional signals only when a demonstrated failure justifies them:

- Whether the candidate is original evidence or a secondary paraphrase.
- Whether adjacent context is likely necessary to interpret it.
- Whether it contributes information not already present in selected candidates.
- Whether it records a later supersession or an earlier state.

Temporal status and novelty may require pairwise or grouped comparison rather than isolated chunk classification. They should not be folded into the first contract merely because the classifier can emit more fields.

## Context Selection

Classification scores do not directly determine truth or deletion. A deterministic selector applies the actual context budget and should account for more than score ordering.

The selector should:

- Reserve space for strong direct lexical or semantic hits so classifier error cannot displace every baseline result.
- Prefer evidence diversity across sources, time ranges, and candidate clusters rather than filling the budget with near-duplicates.
- Admit raw source excerpts rather than classifier-written summaries.
- Preserve stable handles that allow the agent to expand a window, inspect a complete document, or request the next tranche.
- Report how many candidates were considered and omitted so the bounded result is not mistaken for exhaustive evidence.

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

## Potential Follow-Up: Claim-Source Audit

The most promising second use is a narrow entailment audit over generated memory claims and their cited source ranges. The decision question would distinguish direct support, partial support, contradiction, and absence of support. A questionable result would route the claim to a stronger reconciliation or regeneration pass; it would not silently approve or reject the claim.

This experiment directly targets the provenance-laundering failure observed when a map author used uncitable recent evidence but attached older citations. It should remain separate from reranking so the value and failure modes of each decision contract can be measured independently.

## Decision Gate

Proceed beyond a disposable reranking evaluation only if the classifier materially improves canonical-evidence recall per admitted token, reduces redundant context, or reduces follow-up retrieval without introducing unacceptable misses. If lexical or hybrid ranking performs equivalently under the same budget, retain the simpler retrieval path and keep the generic decision runtime available for other uses.
