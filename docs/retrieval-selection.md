# Initial retrieval-backend selection

## Decision

Issue 9 selects `BM25Retriever` as the initial, replaceable retrieval backend.
This is an MVP architecture decision, not a claim that BM25 is permanently
optimal or superior to semantic, dense, or hybrid retrieval on real economics
research questions. No default factory, CLI wiring, persisted index, generation
integration, or second adapter is introduced by this decision.

The existing `Retriever` protocol remains the application boundary. A future
adapter must satisfy the same evidence, validation, determinism, offline, and
privacy contracts and can replace BM25 without changing domain evidence types.

## Rationale

BM25 is selected now because the combined evidence favors the smallest complete
local baseline:

- it passes every frozen `synthetic-economics-v1` regression gate without
  tuning;
- it is pure Python and uses no production dependency, model, downloaded
  artifact, paid API, API key, Docker service, GPU, or persisted index;
- ordinary retrieval performs no filesystem or network I/O;
- its ranks, evidence identities, duplicate suppression, and tie-breaking are
  deterministic and already covered by the retrieval contract; and
- it has substantially lower installation, licensing, portability, privacy,
  and maintenance burden than a learned adapter.

A second lexical adapter, such as TF-IDF, would provide little new decision
evidence because it shares BM25's main lexical limitation. A learned dense
adapter would provide a meaningfully different comparison, but would also add
native dependencies and a model-artifact lifecycle. The frozen benchmark has
only five synthetic papers and fifteen passages, so it is not sufficient by
itself to justify that burden or select a learned adapter.

If the decision is reopened, the candidate must be reviewed for exact package
dependencies; CPU and optional-GPU behavior; artifact source, license,
redistribution status, size, checksum, and update policy; offline installation
and local-only runtime behavior; memory and disk use; initialization and query
latency; cross-platform determinism; and maintenance cost. A broader comparison
benchmark must be separately versioned and approved rather than changing the
Issue 8 fixture.

## Frozen quality evidence

The approved Issue 8 benchmark, corpus fingerprint, 25 queries, binary
judgments, metrics, cutoffs, thresholds, and BM25 configuration remain
unchanged. The untuned `bm25-v1` results are:

| k | Hit Rate | Macro Recall | MRR |
|---:|---:|---:|---:|
| 1 | 0.680000 | 0.573333 | 0.680000 |
| 3 | 0.960000 | 0.880000 | 0.806667 |
| 5 | 0.960000 | 0.933333 | 0.806667 |

The cross-platform stable-result digest is
`sha256:13766bd01249f0c595f8b39ad6617fa78eade2bbb1710042a8ba407cc236e0ee`.
Its canonical payload contains, in benchmark order:

- `query_id`;
- each returned `passage_id`;
- its contiguous rank; and
- `retrieval_method`.

The digest deliberately excludes raw floating-point scores, aggregate metrics,
timings, memory observations, and machine metadata. Scores must still be finite,
contract-valid, and exactly repeatable within a local measurement run. This
allows supported platforms to gate stable ranked results without treating
minor cross-platform floating-point representations as different retrieval
identities.

The retained benchmark headroom is also unchanged: `housing-limitation` has no
judged hit at `k=5`, while the elections and BRT synthesis cases each retrieve
two of three judged passages. The benchmark is a frozen regression and
comparison tool, not an independent estimate of real-world quality.

## Correctness gates and resource observations

Correctness and resource evidence are intentionally separate.

CI may gate:

- the frozen corpus fingerprint, queries, judgments, metrics, and thresholds;
- valid evidence, finite scores, ranks, methods, and offline behavior;
- local repeatability; and
- the stable ranked-result digest.

CI must not gate initialization time, query latency, Python heap, process RSS,
or comparisons between machine profiles. Those values are observations and can
vary with hardware, operating system, Python build, system load, and power
state.

`econ_paper_cli.evaluation.resources` provides the backend-independent
measurement interface:

- `TimingSummary` reports sample count, minimum, median, nearest-rank p95, and
  maximum durations in seconds;
- `MachineProfile` records only standard-library machine metadata and uses
  explicit unavailable values;
- `RetrievalResourceObservations` separates protocol metadata, timing, Python
  heap, and process RSS and provides JSON-compatible `to_mapping()` output;
- `stable_retrieval_result_digest` hashes only stable ranked outputs;
- `measure_retriever_resources` accepts a retriever factory, validated
  benchmark, and corpus; and
- `build_synthetic_scaling_corpus` creates deterministic in-memory text variants
  for computational scaling only, never quality evaluation.

The measurement helper performs at least two measured passes and rejects
changing ranked identities, ranks, methods, or local scores. It uses
`validate_retrieval_results` and verifies returned passages against the exact
corpus snapshot.

## Offline measurement procedure

Run the full non-CI procedure from an installed development checkout:

```bash
python scripts/measure_retrieval.py
```

The default procedure performs:

- five fresh retriever initialization observations;
- one untimed warm-up pass;
- thirty measured passes over all 25 frozen queries; and
- separate resource-only runs over deterministic 1,000- and 10,000-passage
  synthetic scaling corpora.

Use `--no-scaling` to measure only the frozen corpus, repeat
`--scale-passages COUNT` to select other resource-only sizes, or `--output PATH`
to write the JSON report. Overrides exist for local diagnostics, but published
observations should use the default protocol and record any deviation.

The script first evaluates the frozen correctness gates. It does not collect
resource observations when those gates fail. Resource values never determine
its correctness exit status after the gates pass.

Per-query timing includes the adapter call and retrieval-result contract
validation. Timing collection runs without `tracemalloc` instrumentation.
`python_heap_peak_bytes` is collected in a separate untimed query pass and is
the peak traced Python allocation during that pass; it is not total process
memory. `process_peak_rss_bytes` uses the process-lifetime peak from
`resource.getrusage` only where its units are defined here: Linux KiB are
normalized to bytes and macOS bytes are retained. Comparable RSS runs therefore
require a fresh process. On Windows and unsupported platforms, process RSS is
`null` with an actionable `process_rss_status`; no dependency is added and
Python heap is not substituted. Total physical memory and power mode are
likewise left unavailable when the standard library cannot report them
reliably.

The synthetic scaling corpora retain the redistributable source passages and
append deterministic synthetic variants in memory. They measure computational
scaling only. Their rankings and relevance metrics must not be presented as
retrieval-quality evidence.

## Hardware guidance

No physical-machine profile is committed for Issue 9. The managed development
environment cannot be established as a representative physical low-spec
machine, so minimum CPU, RAM, latency, and corpus-size requirements remain
pending. This does not block the initial BM25 selection because BM25 adds no
runtime dependency or artifact requirement.

Future user-facing hardware guidance requires complete JSON observations from
named machines, including operating-system version, architecture, CPU identity,
logical CPU count, installed memory when available, Python implementation and
version, power mode when known, exact corpus size, protocol counts, and any
unavailable fields. At least one genuinely lower-spec CPU profile should be
measured before publishing minimum recommendations. Hosted CI timing is not a
substitute for that evidence.

## Revisit conditions

Reconsider the initial default when one or more of these conditions holds:

- representative queries show systematic lexical mismatch;
- measured BM25 scaling is unsuitable for the approved target corpus;
- a separately approved broader benchmark exists;
- a candidate's dependency and artifact lifecycle satisfies the local-first
  constraints; and
- that candidate shows material, query-level value without unacceptable
  portability, privacy, licensing, resource, or maintenance costs.
