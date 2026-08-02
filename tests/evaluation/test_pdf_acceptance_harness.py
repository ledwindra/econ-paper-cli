"""Strict opt-in end-to-end acceptance harness for PDF section detection benchmarks.

This harness executes over local benchmark PDFs when ECONPAPERS_TEST_ACCEPTANCE_DIR
is set. When unset, tests skip deterministically in standard CI.
"""

import os
from pathlib import Path

import pytest

from econ_paper_cli.adapters.bm25 import BM25Retriever
from econ_paper_cli.adapters.pypdf_extractor import PyPDFExtractor
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    DEFAULT_PDF_SECTION_SETTINGS,
    PDFConversionSettings,
)
from econ_paper_cli.services.early_section_library import (
    project_early_section_library_record,
)
from econ_paper_cli.services.pdf_conversion import convert_pdf_early_sections
from econ_paper_cli.services.pdf_section_detection import detect_pdf_sections

ACCEPTANCE_ENV_VAR = "ECONPAPERS_TEST_ACCEPTANCE_DIR"
REQUIRED_CASES = ("case_a", "case_b", "case_c", "case_d", "case_e", "case_f")


def test_pdf_acceptance_harness_opt_in(tmp_path: Path) -> None:
    env_dir = os.getenv(ACCEPTANCE_ENV_VAR)
    if not env_dir:
        pytest.skip(
            f"{ACCEPTANCE_ENV_VAR} is not set. Skipping real PDF acceptance harness."
        )

    acceptance_path = Path(env_dir).resolve()
    papers_dir = acceptance_path / "papers"
    if not acceptance_path.exists():
        pytest.fail(f"Acceptance directory does not exist: {acceptance_path}")
    if not papers_dir.exists():
        pytest.fail(f"Acceptance papers directory does not exist: {papers_dir}")

    # Find PDF files matching required cases
    pdf_files = list(papers_dir.glob("*.pdf"))
    if not pdf_files:
        pytest.fail(f"No PDF files found in acceptance papers directory: {papers_dir}")

    case_matches: dict[str, list[Path]] = {case_id: [] for case_id in REQUIRED_CASES}
    for pdf_file in pdf_files:
        name_lower = pdf_file.name.lower()
        for case_id in REQUIRED_CASES:
            if case_id in name_lower:
                case_matches[case_id].append(pdf_file)

    missing_cases = [
        case_id for case_id, matches in case_matches.items() if not matches
    ]
    if missing_cases:
        pytest.fail(
            f"Acceptance harness missing required benchmark cases: {missing_cases}"
        )

    ambiguous_cases = [
        case_id for case_id, matches in case_matches.items() if len(matches) > 1
    ]
    if ambiguous_cases:
        pytest.fail(
            f"Acceptance harness found ambiguous matching PDFs for cases: {ambiguous_cases}"
        )

    extractor = PyPDFExtractor()
    conversion_settings = PDFConversionSettings()
    db_path = tmp_path / "acceptance_library.sqlite3"
    storage = SQLiteStorage(db_path)
    storage.initialize()

    results_summary: dict[str, str] = {}

    for case_id in REQUIRED_CASES:
        pdf_path = case_matches[case_id][0]
        # 1. Extraction
        extraction = extractor.extract(pdf_path)
        assert extraction.page_count > 0, f"[{case_id}] Extraction produced 0 pages"

        # 2. Section Detection
        detection = detect_pdf_sections(
            extraction, settings=DEFAULT_PDF_SECTION_SETTINGS
        )
        assert len(detection.sections) > 0, (
            f"[{case_id}] Section detection produced 0 sections"
        )

        # 3. Early Section Conversion
        checksum = "a" * 64
        conversion = convert_pdf_early_sections(
            extraction,
            detection,
            content_checksum=checksum,
            settings=conversion_settings,
        )
        assert conversion.markdown is not None, (
            f"[{case_id}] Early section conversion failed"
        )

        # 4. Storage Projection and DB Save
        record = project_early_section_library_record(
            extraction,
            detection,
            conversion,
            source_file_size=pdf_path.stat().st_size,
            timestamp="2026-08-02T12:00:00Z",
        )
        storage.save_early_section_record(record)

        # 5. DB Verification & Corpus Search
        stored = storage.get_early_section_record(record.paper.paper_id)
        assert stored == record, f"[{case_id}] Database roundtrip mismatch"

        results_summary[case_id] = "PASSED"

    corpus = storage.load_corpus()
    assert len(corpus.papers) == len(REQUIRED_CASES)
    retriever = BM25Retriever(corpus)
    search_results = retriever.search("policy strategy model", top_k=3)
    assert len(search_results) > 0

    storage.close()
    assert all(status == "PASSED" for status in results_summary.values())
