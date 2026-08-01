"""Service layer for PDF discovery and ingestion preflight."""

import hashlib
from pathlib import Path

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


def compute_file_sha256(path: Path, chunk_size: int = 65536) -> tuple[int, str]:
    """Calculate file size and lowercase SHA-256 hex digest for a file.

    Args:
        path: Resolved file path.
        chunk_size: Size of read chunks in bytes.

    Returns:
        Tuple of (size_bytes, sha256_hex_str).

    Raises:
        IngestionPermissionError: If permission is denied.
        IngestionReadError: If an OS read error occurs.
    """
    try:
        size = path.stat().st_size
    except PermissionError as err:
        raise IngestionPermissionError(
            f"Permission denied inspecting file '{path}': {err}."
        ) from err
    except OSError as err:
        raise IngestionReadError(
            f"Read error inspecting file '{path}': {err}."
        ) from err

    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
    except PermissionError as err:
        raise IngestionPermissionError(
            f"Permission denied reading file '{path}': {err}."
        ) from err
    except OSError as err:
        raise IngestionReadError(f"Read error reading file '{path}': {err}.") from err

    return size, hasher.hexdigest().lower()


def run_ingestion_preflight(
    target_path: str | Path,
    storage: StorageBackend | None = None,
) -> IngestionPreflightResult:
    """Discover PDF files deterministically and compute preflight status.

    Args:
        target_path: Explicit path to a PDF file or directory.
        storage: Optional StorageBackend to check for existing stored records.

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
    except PermissionError as err:
        raise IngestionPermissionError(
            f"Permission denied accessing '{expanded_path}': {err}."
        ) from err
    except OSError as err:
        raise IngestionReadError(
            f"Read error checking path '{expanded_path}': {err}."
        ) from err

    resolved_target = expanded_path.resolve()

    if resolved_target.is_file():
        if resolved_target.suffix.lower() != ".pdf":
            raise IngestionUnsupportedFileError(
                f"Specified file '{resolved_target}' is not a supported PDF document (.pdf)."
            )
        pdf_paths = [resolved_target]
    elif resolved_target.is_dir():
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

    # Process discovered PDF files and check batch deduplication & storage state
    candidates: list[PreflightCandidate] = []
    seen_checksums: dict[str, Path] = {}

    new_count = 0
    stored_count = 0
    batch_dup_count = 0

    for pdf_path in pdf_paths:
        size_bytes, checksum = compute_file_sha256(pdf_path)

        if checksum in seen_checksums:
            is_batch_dup = True
            dup_of = seen_checksums[checksum]
            batch_dup_count += 1
        else:
            is_batch_dup = False
            dup_of = None
            seen_checksums[checksum] = pdf_path

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
                source_path=pdf_path,
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
