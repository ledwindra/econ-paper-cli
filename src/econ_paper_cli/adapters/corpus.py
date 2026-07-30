"""Filesystem adapter for loading synthetic economics fixture corpora."""

import json
from collections.abc import Mapping
from pathlib import Path

from econ_paper_cli.adapters.filesystem import FilesystemAdapterError
from econ_paper_cli.domain import Corpus, DomainError


class CorpusLoadError(FilesystemAdapterError):
    """Base exception for errors encountered while loading a corpus."""


class CorpusFileNotFoundError(CorpusLoadError):
    """Raised when a corpus file does not exist."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Corpus file does not exist at '{path}'.")
        self.path = path


class CorpusNotARegularFileError(CorpusLoadError):
    """Raised when the specified path is a directory or special file, not a regular file."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Path at '{path}' is not a regular file.")
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


class CorpusReadError(CorpusLoadError):
    """Raised when an OS error occurs while reading a corpus file."""

    def __init__(self, path: Path, error: OSError) -> None:
        super().__init__(f"Failed to read corpus file at '{path}': {error}.")
        self.path = path
        self.error = error


class CorpusDomainValidationError(CorpusLoadError):
    """Raised when a corpus file contains data that violates domain contracts."""

    def __init__(self, path: Path, error: DomainError) -> None:
        super().__init__(f"Corpus file at '{path}' failed domain validation: {error}.")
        self.path = path
        self.error = error


def load_corpus_from_file(path: Path) -> Corpus:
    """Load a JSON corpus file into a Corpus domain object.

    Args:
        path: Path to the corpus JSON file.

    Returns:
        The validated Corpus domain object.

    Raises:
        CorpusFileNotFoundError: If the file does not exist.
        CorpusNotARegularFileError: If the path is not a regular file.
        CorpusPermissionError: If permission is denied.
        CorpusEncodingError: If the file is not valid UTF-8.
        CorpusInvalidJsonError: If the file contains invalid JSON or root is not a mapping.
        CorpusReadError: If other OS errors occur during file read.
        CorpusDomainValidationError: If domain validation fails (retaining original DomainError in .error).
    """
    if not isinstance(path, Path):
        raise TypeError("path must be a pathlib.Path instance.")

    try:
        if not path.exists():
            raise CorpusFileNotFoundError(path)
        if not path.is_file():
            raise CorpusNotARegularFileError(path)
    except (CorpusFileNotFoundError, CorpusNotARegularFileError):
        raise
    except PermissionError as error:
        raise CorpusPermissionError(path, error) from error
    except OSError as error:
        raise CorpusReadError(path, error) from error

    try:
        content = path.read_text(encoding="utf-8")
    except PermissionError as error:
        raise CorpusPermissionError(path, error) from error
    except UnicodeDecodeError as error:
        raise CorpusEncodingError(path, error) from error
    except OSError as error:
        raise CorpusReadError(path, error) from error

    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise CorpusInvalidJsonError(path, error) from error

    if not isinstance(data, Mapping):
        raise CorpusInvalidJsonError(
            path, ValueError("Root JSON value must be an object/mapping")
        )

    try:
        return Corpus.from_mapping(data)
    except DomainError as error:
        raise CorpusDomainValidationError(path, error) from error
