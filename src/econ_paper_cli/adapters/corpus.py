"""Filesystem adapter for loading synthetic economics fixture corpora."""

import json
from collections.abc import Mapping
from pathlib import Path

from econ_paper_cli.adapters.filesystem import FilesystemAdapterError
from econ_paper_cli.domain import DomainError, Paper, Passage


class CorpusLoadError(FilesystemAdapterError):
    """Base exception for errors encountered while loading a corpus."""


class CorpusFileNotFoundError(CorpusLoadError):
    """Raised when a corpus file does not exist."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Corpus file does not exist at '{path}'.")
        self.path = path


class CorpusPermissionError(CorpusLoadError):
    """Raised when permission is denied accessing a corpus file."""

    def __init__(self, path: Path, error: PermissionError) -> None:
        super().__init__(
            f"Permission denied accessing corpus file at '{path}': {error}."
        )
        self.path = path
        self.error = error


class CorpusEncodingError(CorpusLoadError):
    """Raised when a corpus file is not valid UTF-8."""

    def __init__(self, path: Path, error: UnicodeDecodeError) -> None:
        super().__init__(f"Corpus file at '{path}' is not valid UTF-8: {error}.")
        self.path = path
        self.error = error


class CorpusInvalidJsonError(CorpusLoadError):
    """Raised when a corpus file contains invalid JSON or root is not an object."""

    def __init__(self, path: Path, error: Exception) -> None:
        super().__init__(f"Corpus file at '{path}' contains invalid JSON: {error}.")
        self.path = path
        self.error = error


class CorpusValidationError(CorpusLoadError):
    """Raised when a corpus file fails domain validation or structural integrity."""

    def __init__(self, path: Path, details: str) -> None:
        super().__init__(f"Corpus file at '{path}' failed validation: {details}")
        self.path = path
        self.details = details


def load_corpus_from_file(path: Path) -> tuple[tuple[Paper, ...], tuple[Passage, ...]]:
    """Load and validate a JSON corpus file into Paper and Passage domain instances.

    Args:
        path: Path to the corpus JSON file.

    Returns:
        A tuple of (tuple of Papers, tuple of Passages).

    Raises:
        CorpusFileNotFoundError: If the file does not exist or is not a file.
        CorpusPermissionError: If permission is denied.
        CorpusEncodingError: If the file is not UTF-8 encoded.
        CorpusInvalidJsonError: If the file is not valid JSON or root is not a mapping.
        CorpusValidationError: If paper/passage domain validation fails or orphan references exist.
    """
    if not isinstance(path, Path):
        raise TypeError("path must be a pathlib.Path instance.")

    if not path.is_file():
        raise CorpusFileNotFoundError(path)

    try:
        content = path.read_text(encoding="utf-8")
    except PermissionError as error:
        raise CorpusPermissionError(path, error) from error
    except UnicodeDecodeError as error:
        raise CorpusEncodingError(path, error) from error
    except OSError as error:
        raise CorpusLoadError(
            f"Failed to read corpus file at '{path}': {error}."
        ) from error

    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise CorpusInvalidJsonError(path, error) from error

    if not isinstance(data, Mapping):
        raise CorpusInvalidJsonError(
            path, TypeError("Root JSON value must be an object/mapping.")
        )

    raw_papers = data.get("papers")
    if not isinstance(raw_papers, list):
        raise CorpusValidationError(path, "'papers' must be a JSON array.")

    raw_passages = data.get("passages")
    if not isinstance(raw_passages, list):
        raise CorpusValidationError(path, "'passages' must be a JSON array.")

    papers: list[Paper] = []
    paper_ids: set[str] = set()
    for index, raw_paper in enumerate(raw_papers):
        if not isinstance(raw_paper, Mapping):
            raise CorpusValidationError(
                path, f"Paper at index {index} must be an object/mapping."
            )
        try:
            paper = Paper.from_mapping(raw_paper)
        except DomainError as error:
            paper_id_str = (
                str(raw_paper.get("paper_id")) if "paper_id" in raw_paper else "unknown"
            )
            raise CorpusValidationError(
                path,
                f"Paper at index {index} ('{paper_id_str}') is invalid: {error}",
            ) from error

        if paper.paper_id in paper_ids:
            raise CorpusValidationError(
                path,
                f"Duplicate paper_id '{paper.paper_id}' detected at index {index}.",
            )
        paper_ids.add(paper.paper_id)
        papers.append(paper)

    passages: list[Passage] = []
    passage_ids: set[str] = set()
    for index, raw_passage in enumerate(raw_passages):
        if not isinstance(raw_passage, Mapping):
            raise CorpusValidationError(
                path, f"Passage at index {index} must be an object/mapping."
            )
        try:
            passage = Passage.from_mapping(raw_passage)
        except DomainError as error:
            passage_id_str = (
                str(raw_passage.get("passage_id"))
                if "passage_id" in raw_passage
                else "unknown"
            )
            raise CorpusValidationError(
                path,
                f"Passage at index {index} ('{passage_id_str}') is invalid: {error}",
            ) from error

        if passage.passage_id in passage_ids:
            raise CorpusValidationError(
                path,
                f"Duplicate passage_id '{passage.passage_id}' detected at index {index}.",
            )
        if passage.paper_id not in paper_ids:
            raise CorpusValidationError(
                path,
                f"Passage '{passage.passage_id}' references unknown paper_id '{passage.paper_id}'.",
            )

        passage_ids.add(passage.passage_id)
        passages.append(passage)

    return (tuple(papers), tuple(passages))
