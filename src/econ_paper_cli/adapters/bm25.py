"""Pure-Python deterministic BM25 retriever adapter.

This module provides `BM25Retriever`, an in-memory sparse retrieval baseline
satisfying the `Retriever` protocol interface.

Key Architectural Invariants:
- Local & Standard-Library Only: Pure Python implementation with zero third-party dependencies.
- One-Time Precomputed Indexing: Corpus term statistics, passage token counts, and document frequencies are computed once during `__init__`. Query-time retrieval does not re-tokenize passages or recalculate corpus statistics.
- Pure Retrieval In-Memory Operation: No filesystem or network I/O occurs during `retrieve(...)`.
- Field Scope: Indexes `Passage.text` only. Paper titles, authors, abstracts, section headings, and metadata are excluded from scoring.
- Exact Tokenizer (`bm25-v1`): Character scan splitting on non-alphanumeric characters (`char.isalnum()`) with Unicode NFKC normalization and casefolding (`str.casefold()`). Numeric tokens are retained; stop words are indexed without stemming or lemmatization.
- BM25 Scoring Variant: Positive IDF ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5)).
- Deterministic Ordering: Scores ordered descending; equal scores use `passage_id` ascending tie-breaker.
- Normalized Lexical Duplicate Suppression: Passages yielding identical complete token sequences are suppressed, retaining the candidate with `passage_id` ascending.
"""

import math
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from econ_paper_cli.domain import Corpus, Passage, RetrievalEvidence
from econ_paper_cli.protocols import RetrievalRequest, validate_retrieval_results


class BM25ConfigurationError(ValueError):
    """Raised when BM25 configuration parameters or corpus input are invalid."""


def _tokenize(text: str) -> tuple[str, ...]:
    """Tokenize text using explicit character scan with char.isalnum().

    Behavior:
    1. Unicode NFKC normalization.
    2. Casefolding (str.casefold()).
    3. Character scan extracting contiguous alphanumeric sequences.
    4. Delimiters (punctuation, symbols, apostrophes, underscores, hyphens) split tokens.
    5. Numeric tokens are retained.
    6. Empty tokens are discarded.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    current: list[str] = []

    for char in normalized:
        if char.isalnum():
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current.clear()

    if current:
        tokens.append("".join(current))

    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class _PassageEntry:
    passage: Passage
    tokens: tuple[str, ...]
    term_counts: Mapping[str, int]
    doc_len: int


class BM25Retriever:
    """Pure-Python deterministic BM25 retriever adapter over an in-memory Corpus."""

    def __init__(
        self,
        corpus: Corpus,
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        """Initialize BM25Retriever and precompute corpus term statistics once.

        Args:
            corpus: Validated Corpus domain object.
            k1: Term frequency saturation parameter (k1 > 0). Default: 1.5.
            b: Document length normalization parameter (0 <= b <= 1). Default: 0.75.
        """
        if not isinstance(corpus, Corpus):
            raise BM25ConfigurationError("corpus must be a Corpus instance.")

        k1_float = _validate_k1(k1)
        b_float = _validate_b(b)

        self._k1: float = k1_float
        self._b: float = b_float
        self._corpus: Corpus = corpus

        # Precompute in-memory corpus statistics once during __init__
        self._num_passages: int = len(corpus.passages)

        entries: list[_PassageEntry] = []
        doc_freqs: dict[str, int] = {}
        total_len = 0

        for passage in corpus.passages:
            tokens = _tokenize(passage.text)
            doc_len = len(tokens)
            total_len += doc_len
            counts = Counter(tokens)
            entries.append(
                _PassageEntry(
                    passage=passage,
                    tokens=tokens,
                    term_counts=counts,
                    doc_len=doc_len,
                )
            )
            for term in counts:
                doc_freqs[term] = doc_freqs.get(term, 0) + 1

        self._avgdl: float = (
            float(total_len) / float(self._num_passages)
            if self._num_passages > 0
            else 0.0
        )
        self._doc_freqs: dict[str, int] = doc_freqs
        self._entries: tuple[_PassageEntry, ...] = tuple(entries)

    def retrieve(
        self,
        request: RetrievalRequest,
    ) -> tuple[RetrievalEvidence, ...]:
        """Retrieve ranked evidence passages matching the request query.

        Args:
            request: Validated RetrievalRequest query.

        Returns:
            Tuple of RetrievalEvidence instances conforming to protocol invariants.
        """
        if not isinstance(request, RetrievalRequest):
            raise TypeError("request must be a RetrievalRequest instance.")

        query_tokens = _tokenize(request.query)
        if not query_tokens or self._num_passages == 0 or self._avgdl == 0.0:
            return ()

        # Deduplicate query terms preserving sequence order
        unique_terms = tuple(dict.fromkeys(query_tokens))

        # Calculate positive IDF for query terms present in corpus
        idfs: dict[str, float] = {}
        for t in unique_terms:
            df_t = self._doc_freqs.get(t, 0)
            if df_t > 0:
                idfs[t] = math.log(
                    1.0 + (self._num_passages - df_t + 0.5) / (df_t + 0.5)
                )

        if not idfs:
            return ()

        # Score candidate passages
        candidates: list[tuple[float, _PassageEntry]] = []
        for entry in self._entries:
            contributions: list[float] = []
            for t, idf in idfs.items():
                tf = entry.term_counts.get(t, 0)
                if tf > 0:
                    denom = tf + self._k1 * (
                        1.0 - self._b + self._b * (entry.doc_len / self._avgdl)
                    )
                    score_t = idf * (tf * (self._k1 + 1.0)) / denom
                    contributions.append(score_t)

            if contributions:
                total_score = math.fsum(contributions)
                if total_score > 0.0:
                    candidates.append((total_score, entry))

        if not candidates:
            return ()

        # Sort: score descending (-score), tie-breaker passage_id ascending
        candidates.sort(key=lambda item: (-item[0], item[1].passage.passage_id))

        # Normalized lexical duplicate suppression
        seen_token_sequences: set[tuple[str, ...]] = set()
        retained: list[tuple[float, _PassageEntry]] = []

        for item in candidates:
            entry_tokens = item[1].tokens
            if entry_tokens not in seen_token_sequences:
                seen_token_sequences.add(entry_tokens)
                retained.append(item)
                if len(retained) == request.top_k:
                    break

        # Construct RetrievalEvidence instances with contiguous 1-based ranks
        results: list[RetrievalEvidence] = [
            RetrievalEvidence(
                passage=item[1].passage,
                score=item[0],
                rank=i + 1,
                retrieval_method="bm25-v1",
            )
            for i, item in enumerate(retained)
        ]

        return validate_retrieval_results(request, tuple(results))


def _validate_k1(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BM25ConfigurationError("k1 must be a positive finite float.")
    float_val = float(value)
    if not math.isfinite(float_val) or float_val <= 0.0:
        raise BM25ConfigurationError("k1 must be a positive finite float (> 0).")
    return float_val


def _validate_b(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BM25ConfigurationError(
            "b must be a finite float between 0 and 1 inclusive."
        )
    float_val = float(value)
    if not math.isfinite(float_val) or float_val < 0.0 or float_val > 1.0:
        raise BM25ConfigurationError(
            "b must be a finite float between 0 and 1 inclusive."
        )
    return float_val
