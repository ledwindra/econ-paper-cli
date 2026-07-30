# Retrieval evaluation

## Scope

Issue 8 adds a deterministic offline regression benchmark for ranked retrieval.
It evaluates the existing untuned `BM25Retriever` against 25 maintainer-approved
synthetic economics questions while keeping the evaluation logic dependent only
on the backend-independent `Retriever` protocol.

The benchmark is not an estimate of real-world retrieval quality, does not show
that BM25 is superior to another adapter, and does not select a permanent default
retriever. The corpus contains only five synthetic papers and fifteen passages.
Future adapters may be run against the same frozen benchmark before any separate
adapter-selection decision.

## Fixture and relevance judgments

The benchmark fixture is
`tests/fixtures/retrieval/synthetic-economics-v1-benchmark.json`. It is
maintainer-authored, licensed CC0-1.0, and tied to the CC0-1.0
`synthetic-economics-v1` corpus. It contains:

- five topic or research-question queries;
- five causal or empirical-method queries;
- five principal-finding or effect-size queries;
- five mechanism, heterogeneity, or limitation queries; and
- five synthesis queries requiring all three passages from a paper.

Queries are natural-language paraphrases rather than copied passage sentences.
Relevance is binary and identified only by stable `passage_id`. Each judgment
includes a short rationale. Changing a query, judgment, cutoff, or threshold
requires explicit review rather than adapting the fixture to current rankings.

## Corpus fingerprint

The benchmark pins exact retrieval-relevant corpus content with a SHA-256
fingerprint. `corpus_retrieval_fingerprint`:

1. sorts passages by `passage_id`, so corpus tuple order is irrelevant;
2. represents each passage with `paper_id`, `passage_id`, and exact `text`;
3. serializes that list as UTF-8 canonical JSON with sorted object keys,
   compact separators, and Unicode preserved; and
4. prefixes the lowercase SHA-256 digest with `sha256:`.

The frozen fingerprint is
`sha256:3af9525b39cbd83576b1563f8ae0cc399ce886d57172485defe0a83ba5cefb48`.
Evaluation validates `corpus_id`, then this fingerprint, then every judged
passage ID before issuing any retrieval request. A text change therefore fails
even when all IDs remain unchanged, with an instruction to review the relevance
judgments and thresholds before accepting a new fingerprint.

The evaluator also checks every returned passage's `passage_id`, `paper_id`, and
exact text against that validated corpus snapshot. This prevents a retriever
configured over different content from being evaluated under the pinned corpus
identity.

The fingerprint intentionally excludes JSON whitespace, passage tuple order,
and metadata that the current retriever does not index. It covers the exact text
and identities that determine retrieval and passage-level judgments.

## Metrics and regression gates

For query set `Q`, relevant set `R_q`, and the first `k` retrieved IDs `D_q(k)`:

- **Hit Rate@k** is the mean of `1` when `D_q(k)` intersects `R_q`, otherwise
  `0`.
- **Macro Recall@k** is the mean of
  `|D_q(k) intersection R_q| / |R_q|`, giving every query equal weight.
- **MRR@k** is the mean reciprocal rank of the first relevant result, or `0`
  when no relevant result occurs by `k`.

The evaluator calculates all three at `k = 1, 3, 5`. Precision is omitted
because sparse judgments would incorrectly penalize unjudged supporting
passages. nDCG is omitted because relevance is binary and mostly single-passage;
MRR plus recall captures the useful ranking distinctions without unjustified
graded judgments.

The approved CI regression gates are:

| Metric | Minimum |
|---|---:|
| Hit Rate@1 | 0.60 |
| Hit Rate@3 | 0.90 |
| Macro Recall@5 | 0.90 |
| MRR@5 | 0.75 |

These thresholds protect the frozen synthetic baseline from regressions. They
are not independent quality estimates or optimization targets.

## Untuned BM25 baseline

With the Issue 7 defaults (`k1=1.5`, `b=0.75`, `bm25-v1`), the frozen benchmark
produces:

| k | Hit Rate | Macro Recall | MRR |
|---:|---:|---:|---:|
| 1 | 0.680000 | 0.573333 | 0.680000 |
| 3 | 0.960000 | 0.880000 | 0.806667 |
| 5 | 0.960000 | 0.933333 | 0.806667 |

At `k=5`, `housing-limitation` retrieves no judged passage. The synthesis cases
for elections and BRT each retrieve two of three relevant passages; the missing
passages are respectively the elections introduction and BRT findings passage.
These misses are retained as benchmark headroom and must not be removed by
rewriting queries or tuning BM25 within Issue 8.

## Execution and effects

The evaluator receives a validated `RetrievalBenchmark`, configured `Retriever`,
and validated `Corpus`. It retrieves once per query at the maximum cutoff and
derives smaller cutoffs from the same returned tuple. Evaluation itself performs
no filesystem or network I/O and requires no downloads, models, GPU, paid API,
persisted index, or CLI integration.
