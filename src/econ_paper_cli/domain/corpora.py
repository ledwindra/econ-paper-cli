"""Pure domain contract for economic literature corpora."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from econ_paper_cli.domain.errors import DomainError
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.passages import Passage

_CORPUS_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_CORPUS_FIELDS = frozenset({"schema_version", "corpus_id", "papers", "passages"})


class CorpusValidationError(DomainError):
    """Raised when corpus data violates domain rules or cross-record invariants."""


@dataclass(frozen=True, slots=True)
class Corpus:
    """Immutable domain representation of an economic literature corpus."""

    schema_version: int
    corpus_id: str
    papers: tuple[Paper, ...]
    passages: tuple[Passage, ...]

    def __post_init__(self) -> None:
        """Validate direct construction."""
        _validate_schema_version(self.schema_version)
        _validate_corpus_id(self.corpus_id)
        _validate_papers(self.papers)
        _validate_passages(self.passages)
        _validate_cross_record_invariants(self.papers, self.passages)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "Corpus":
        """Validate and construct a Corpus from a JSON-compatible mapping."""
        if not isinstance(data, Mapping):
            raise CorpusValidationError("Corpus metadata must be a mapping.")
        if any(not isinstance(field, str) for field in data):
            raise CorpusValidationError("Corpus field names must be strings.")

        provided_fields = set(data)
        missing_fields = sorted(_CORPUS_FIELDS - provided_fields)
        if missing_fields:
            raise CorpusValidationError(
                "Corpus metadata is missing required fields: "
                + ", ".join(missing_fields)
                + "."
            )

        unknown_fields = sorted(provided_fields - _CORPUS_FIELDS)
        if unknown_fields:
            raise CorpusValidationError(
                "Corpus metadata contains unknown fields: "
                + ", ".join(unknown_fields)
                + "."
            )

        raw_papers = data["papers"]
        if not isinstance(raw_papers, (list, tuple)):
            raise CorpusValidationError("papers must be a non-empty sequence.")
        papers_seq = cast(Sequence[object], raw_papers)

        papers: list[Paper] = []
        for raw_paper in papers_seq:
            if isinstance(raw_paper, Mapping):
                papers.append(Paper.from_mapping(cast(Mapping[str, object], raw_paper)))
            elif isinstance(raw_paper, Paper):
                papers.append(raw_paper)
            else:
                raise CorpusValidationError(
                    "Each paper must be a Paper object or mapping."
                )

        raw_passages = data["passages"]
        if not isinstance(raw_passages, (list, tuple)):
            raise CorpusValidationError("passages must be a non-empty sequence.")
        passages_seq = cast(Sequence[object], raw_passages)

        passages: list[Passage] = []
        for raw_passage in passages_seq:
            if isinstance(raw_passage, Mapping):
                passages.append(
                    Passage.from_mapping(cast(Mapping[str, object], raw_passage))
                )
            elif isinstance(raw_passage, Passage):
                passages.append(raw_passage)
            else:
                raise CorpusValidationError(
                    "Each passage must be a Passage object or mapping."
                )

        return cls(
            schema_version=cast(int, data["schema_version"]),
            corpus_id=cast(str, data["corpus_id"]),
            papers=tuple(papers),
            passages=tuple(passages),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "corpus_id": self.corpus_id,
            "papers": [paper.to_mapping() for paper in self.papers],
            "passages": [passage.to_mapping() for passage in self.passages],
            "schema_version": self.schema_version,
        }


def _validate_schema_version(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise CorpusValidationError("schema_version must be the integer 1.")


def _validate_corpus_id(value: object) -> None:
    if not isinstance(value, str) or _CORPUS_ID_PATTERN.fullmatch(value) is None:
        raise CorpusValidationError(
            "corpus_id must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
        )


def _validate_papers(value: object) -> None:
    if not isinstance(value, tuple) or not value:
        raise CorpusValidationError(
            "papers must be a non-empty tuple of Paper objects."
        )
    for paper in value:
        if not isinstance(paper, Paper):
            raise CorpusValidationError("Each item in papers must be a Paper instance.")


def _validate_passages(value: object) -> None:
    if not isinstance(value, tuple) or not value:
        raise CorpusValidationError(
            "passages must be a non-empty tuple of Passage objects."
        )
    for passage in value:
        if not isinstance(passage, Passage):
            raise CorpusValidationError(
                "Each item in passages must be a Passage instance."
            )


def _validate_cross_record_invariants(
    papers: tuple[Paper, ...], passages: tuple[Passage, ...]
) -> None:
    seen_paper_ids: set[str] = set()
    for paper in papers:
        if paper.paper_id in seen_paper_ids:
            raise CorpusValidationError(
                f"Duplicate paper_id '{paper.paper_id}' detected in corpus."
            )
        seen_paper_ids.add(paper.paper_id)

    seen_passage_ids: set[str] = set()
    seen_ordinals: set[tuple[str, int]] = set()

    for passage in passages:
        if passage.passage_id in seen_passage_ids:
            raise CorpusValidationError(
                f"Duplicate passage_id '{passage.passage_id}' detected in corpus."
            )
        seen_passage_ids.add(passage.passage_id)

        if passage.paper_id not in seen_paper_ids:
            raise CorpusValidationError(
                f"Passage '{passage.passage_id}' references unknown paper_id '{passage.paper_id}'."
            )

        ordinal_key = (passage.paper_id, passage.ordinal_position)
        if ordinal_key in seen_ordinals:
            raise CorpusValidationError(
                f"Duplicate ordinal_position {passage.ordinal_position} for paper '{passage.paper_id}'."
            )
        seen_ordinals.add(ordinal_key)
