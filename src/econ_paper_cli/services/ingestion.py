"""Service layer for PDF discovery and ingestion preflight."""

from collections.abc import Callable
from pathlib import Path

from econ_paper_cli.adapters.filesystem import (
    ArtifactFileNotFoundError,
    ArtifactNotARegularFileError,
    FileInspectionResult,
    VerificationPermissionError,
    VerificationReadError,
    inspect_local_file,
)
from econ_paper_cli.domain.errors import (
    IngestionEmptyDirectoryError,
    IngestionInvalidPathError,
    IngestionPathNotFoundError,
    IngestionPermissionError,
    IngestionReadError,
    IngestionUnsupportedFileError,
)
from econ_paper_cli.domain.ingestion import (
    IngestionPreflightResult,
    PreflightCandidate,
)
from econ_paper_cli.protocols.storage import StorageBackend


def run_ingestion_preflight(
    target_path: str | Path,
    storage: StorageBackend | None = None,
    file_inspector: Callable[[Path], FileInspectionResult] = inspect_local_file,
) -> IngestionPreflightResult:
    """Discover PDF files deterministically and compute preflight status.

    Args:
        target_path: Explicit path to a PDF file or directory.
        storage: Optional StorageBackend to check for existing stored records.
        file_inspector: Callable adapter to inspect local file (path -> FileInspectionResult).

    Returns:
        An immutable IngestionPreflightResult.

    Raises:
        IngestionPathNotFoundError: If target_path does not exist.
        IngestionInvalidPathError: If target_path is not a regular file or directory.
        IngestionUnsupportedFileError: If target_path is a file but not a .pdf file.
        IngestionEmptyDirectoryError: If target_path is a directory with no .pdf files.
        IngestionPermissionError: If permission is denied.
        IngestionReadError: If an OS read error occurs.
    """
    raw_path = Path(target_path) if isinstance(target_path, str) else target_path
    expanded_path = raw_path.expanduser()

    try:
        if not expanded_path.exists():
            raise IngestionPathNotFoundError(
                f"Target path for ingestion does not exist: '{expanded_path}'."
            )
        resolved_target = expanded_path.resolve()
        is_file = resolved_target.is_file()
        is_dir = resolved_target.is_dir()
    except IngestionPathNotFoundError:
        raise
    except PermissionError as err:
        raise IngestionPermissionError(
            f"Permission denied accessing '{expanded_path}': {err}."
        ) from err
    except OSError as err:
        raise IngestionReadError(
            f"Read error checking path '{expanded_path}': {err}."
        ) from err

    if is_file:
        if resolved_target.suffix.lower() != ".pdf":
            raise IngestionUnsupportedFileError(
                f"Specified file '{resolved_target}' is not a supported PDF document (.pdf)."
            )
        pdf_paths = [resolved_target]
    elif is_dir:
        try:
            # Recursively discover all regular files ending with .pdf (case-insensitive)
            discovered = [
                p.resolve()
                for p in resolved_target.rglob("*")
                if p.is_file() and p.suffix.lower() == ".pdf"
            ]
        except PermissionError as err:
            raise IngestionPermissionError(
                f"Permission denied scanning directory '{resolved_target}': {err}."
            ) from err
        except OSError as err:
            raise IngestionReadError(
                f"Read error scanning directory '{resolved_target}': {err}."
            ) from err

        if not discovered:
            raise IngestionEmptyDirectoryError(
                f"No supported PDF files (.pdf) were found in directory '{resolved_target}'."
            )

        # Deterministic ordering by path string
        pdf_paths = sorted(discovered, key=lambda p: str(p))
    else:
        raise IngestionInvalidPathError(
            f"Target path '{resolved_target}' is not a regular file or directory."
        )

    # Process discovered PDF files using file_inspector adapter
    candidates: list[PreflightCandidate] = []
    seen_checksums: dict[str, Path] = {}

    new_count = 0
    stored_count = 0
    batch_dup_count = 0

    for pdf_path in pdf_paths:
        try:
            inspection = file_inspector(pdf_path)
        except ArtifactFileNotFoundError as err:
            raise IngestionPathNotFoundError(
                f"Candidate file does not exist: '{pdf_path}'."
            ) from err
        except ArtifactNotARegularFileError as err:
            raise IngestionInvalidPathError(
                f"Candidate path is not a regular file: '{pdf_path}'."
            ) from err
        except VerificationPermissionError as err:
            raise IngestionPermissionError(
                f"Permission denied reading candidate file '{pdf_path}': {err}."
            ) from err
        except VerificationReadError as err:
            raise IngestionReadError(
                f"Read error reading candidate file '{pdf_path}': {err}."
            ) from err

        size_bytes = inspection.size_bytes
        checksum = inspection.sha256

        if checksum in seen_checksums:
            is_batch_dup = True
            dup_of = seen_checksums[checksum]
            batch_dup_count += 1
        else:
            is_batch_dup = False
            dup_of = None
            seen_checksums[checksum] = inspection.file_path

        is_stored = False
        if storage is not None:
            try:
                record = storage.get_paper_record_by_checksum(checksum)
                if record is not None:
                    is_stored = True
            except Exception as err:
                raise IngestionReadError(
                    f"Failed to query storage by checksum for '{pdf_path}': {err}."
                ) from err

        if is_stored:
            stored_count += 1
        elif not is_batch_dup:
            new_count += 1

        candidates.append(
            PreflightCandidate(
                source_path=inspection.file_path,
                file_size_bytes=size_bytes,
                content_checksum=checksum,
                is_stored=is_stored,
                is_batch_duplicate=is_batch_dup,
                duplicate_of_path=dup_of,
            )
        )

    return IngestionPreflightResult(
        target_path=resolved_target,
        candidates=tuple(candidates),
        new_candidate_count=new_count,
        stored_candidate_count=stored_count,
        batch_duplicate_count=batch_dup_count,
        total_candidate_count=len(candidates),
    )
