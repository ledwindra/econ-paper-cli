"""Strict opt-in end-to-end acceptance harness for real PDF section detection benchmarks.

This harness evaluates real benchmark PDFs against strict structural contracts,
disjoint span math, SQLite restart persistence, BM25 retrieval, citation grounding,
and analysis reuse/replacement.

Execution is gated on the ECONPAPERS_TEST_ACCEPTANCE_DIR environment variable.
When unset, tests skip deterministically in standard CI.
A synthetic contract test executes the harness pipeline in normal CI.
"""

import dataclasses
import hashlib
import os
from pathlib import Path

import pytest

from econ_paper_cli.adapters.bm25 import BM25Retriever
from econ_paper_cli.adapters.pypdf_extractor import PyPDFExtractor
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    DEFAULT_PDF_SECTION_SETTINGS,
    DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
    Citation,
    PDFConversionSettings,
    PDFSectionDetectionMethod,
    PDFSectionKind,
    PDFSectionSettings,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisSettings,
    compute_analysis_id,
)
from econ_paper_cli.protocols.generation import (
    AbstentionReason,
    FindingKind,
    GenerationRequest,
    GenerationResponse,
    Generator,
)
from econ_paper_cli.services.early_section_library import (
    project_early_section_library_record,
)
from econ_paper_cli.services.pdf_conversion import convert_pdf_early_sections
from econ_paper_cli.services.pdf_section_detection import detect_pdf_sections
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper

ACCEPTANCE_ENV_VAR = "ECONPAPERS_TEST_ACCEPTANCE_DIR"
ACCEPTANCE_PAPER_DIR_ENV_VAR = "ECONPAPERS_ACCEPTANCE_PAPER_DIR"


@dataclasses.dataclass(frozen=True, slots=True)
class BenchmarkCaseManifest:
    case_id: str
    patterns: tuple[str, ...]
    expected_abstract_method: PDFSectionDetectionMethod
    expected_intro_method: PDFSectionDetectionMethod


BENCHMARK_MANIFEST = (
    BenchmarkCaseManifest(
        case_id="case_a",
        patterns=("1-s2.0-S009411902600001X-main.pdf", "*case_a*.pdf"),
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
    ),
    BenchmarkCaseManifest(
        case_id="case_b",
        patterns=("lbaf056.pdf", "*case_b*.pdf"),
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
    ),
    BenchmarkCaseManifest(
        case_id="case_c",
        patterns=("*Aspelund*.pdf", "*Russo*.pdf", "*case_c*.pdf"),
        expected_abstract_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
        expected_intro_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
    ),
    BenchmarkCaseManifest(
        case_id="case_d",
        patterns=("*Regional*.pdf", "*regional*.pdf", "*case_d*.pdf"),
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
    ),
    BenchmarkCaseManifest(
        case_id="case_e",
        patterns=("*gravity*.pdf", "*trade*.pdf", "*case_e*.pdf"),
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
    ),
    BenchmarkCaseManifest(
        case_id="case_f",
        patterns=("427465.pdf", "*case_f*.pdf"),
        expected_abstract_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
    ),
)


class DeterministicMockGenerator(Generator):
    """Fake generator producing deterministic citation evidence matching the Generator protocol."""

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        if not request.evidence:
            return GenerationResponse(
                answer_text="Insufficient evidence.",
                citations=(),
                generation_method="deterministic-mock",
                abstained=True,
                abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
                finding_kinds=(),
            )
        first_ev = request.evidence[0]
        citation = Citation(
            citation_id="cit-1",
            paper_id=first_ev.passage.paper_id,
            passage_id=first_ev.passage.passage_id,
        )
        return GenerationResponse(
            answer_text="Grounded synthesis response.",
            citations=(citation,),
            generation_method="deterministic-mock",
            abstained=False,
            abstention_reason=None,
            finding_kinds=(FindingKind.DESCRIPTIVE,),
        )


def run_pdf_acceptance_harness(papers_dir: Path, db_dir: Path) -> dict[str, str]:
    """Execute full 6-paper benchmark acceptance suite over a papers directory."""
    if not papers_dir.exists():
        pytest.fail(f"Acceptance papers directory does not exist: {papers_dir}")

    matched_files: dict[str, Path] = {}
    assigned_files: set[Path] = set()

    for manifest_entry in BENCHMARK_MANIFEST:
        candidates: list[Path] = []
        for pattern in manifest_entry.patterns:
            candidates.extend(papers_dir.glob(pattern))

        unassigned = [c for c in candidates if c not in assigned_files]
        if not unassigned:
            pytest.fail(
                f"Missing benchmark PDF for {manifest_entry.case_id} matching patterns {manifest_entry.patterns} in {papers_dir}"
            )
        target = unassigned[0]
        matched_files[manifest_entry.case_id] = target
        assigned_files.add(target)

    assert len(matched_files) == 6

    extractor = PyPDFExtractor()
    generator = DeterministicMockGenerator()
    db_path = db_dir / "acceptance_library.sqlite3"
    storage = SQLiteStorage(db_path)
    storage.initialize()

    case_summaries: dict[str, str] = {}

    for manifest_entry in BENCHMARK_MANIFEST:
        case_id = manifest_entry.case_id
        pdf_path = matched_files[case_id]
        pdf_bytes = pdf_path.read_bytes()
        checksum = hashlib.sha256(pdf_bytes).hexdigest()

        # 1. Extraction
        extraction = extractor.extract(pdf_path)
        assert extraction.page_count > 0, f"[{case_id}] Extraction produced 0 pages"

        # 2. Section Detection & Structural Verification
        detection = detect_pdf_sections(
            extraction, settings=DEFAULT_PDF_SECTION_SETTINGS
        )
        assert len(detection.sections) >= 2, f"[{case_id}] Expected at least 2 sections"

        section_kinds = [s.kind for s in detection.sections]
        assert PDFSectionKind.ABSTRACT in section_kinds, f"[{case_id}] Missing ABSTRACT"
        assert PDFSectionKind.INTRODUCTION in section_kinds, (
            f"[{case_id}] Missing INTRODUCTION"
        )

        abs_sec = next(
            s for s in detection.sections if s.kind is PDFSectionKind.ABSTRACT
        )
        intro_sec = next(
            s for s in detection.sections if s.kind is PDFSectionKind.INTRODUCTION
        )

        assert abs_sec.detection_method == manifest_entry.expected_abstract_method
        assert intro_sec.detection_method == manifest_entry.expected_intro_method

        # Verify disjoint source span concatenation
        for section in (abs_sec, intro_sec):
            reconstructed = "".join(
                extraction.pages[span.page_number - 1].text[
                    span.start_character_offset : span.end_character_offset
                ]
                for span in section.spans
            )
            assert section.text == reconstructed

        # 3. Conversion & Ingestion
        conversion_settings = PDFConversionSettings(
            section_policy_version="pdf-section-detection-v2"
        )
        conversion = convert_pdf_early_sections(
            extraction,
            detection,
            content_checksum=checksum,
            settings=conversion_settings,
        )
        assert conversion.markdown is not None

        early_record = project_early_section_library_record(
            extraction,
            detection,
            conversion,
            source_file_size=pdf_path.stat().st_size,
            timestamp="2026-08-02T12:00:00Z",
        )
        storage.save_early_section_record(early_record)

        # 4. Single Paper Analysis Pipeline
        analysis_result = analyze_single_paper(
            pdf_path,
            extractor,
            generator,
            settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
        )
        assert analysis_result.preflight_result.passed
        analysis_record = SinglePaperAnalysisRecord.from_result(analysis_result)
        storage.save_single_paper_analysis(analysis_record)

        case_summaries[case_id] = "PASSED"

    # 5. Database Close & Reopen Verification
    storage.close()

    reopened_storage = SQLiteStorage(db_path)
    reopened_storage.initialize()

    corpus = reopened_storage.load_corpus()
    assert len(corpus.papers) == 6
    assert len(corpus.passages) > 0

    # 6. BM25 Retrieval & Generator Citation Verification
    retriever = BM25Retriever(corpus)
    retrieved_passages = retriever.search("economic model analysis", top_k=5)
    assert len(retrieved_passages) > 0

    evidence = tuple(
        retrieved_passages[0]
        .passages[0]
        .to_retrieval_evidence(retrieved_passages[0].score)
        for _ in [0]
    )
    request = GenerationRequest(question="What is the model?", evidence=evidence)
    response = generator.generate(request)
    assert not response.abstained
    assert len(response.citations) == 1

    # 7. Repeated Analysis Reuse & Replacement Verification
    sample_pdf = matched_files["case_a"]
    checksum_sample = hashlib.sha256(sample_pdf.read_bytes()).hexdigest()
    reused_analysis_id = compute_analysis_id(
        checksum_sample, DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS, source_path=sample_pdf
    )
    reused_record = reopened_storage.get_single_paper_analysis(reused_analysis_id)
    assert reused_record is not None

    modified_settings = SinglePaperAnalysisSettings(
        section_settings=PDFSectionSettings(max_candidate_search_lines=150)
    )
    modified_result = analyze_single_paper(
        sample_pdf,
        extractor,
        generator,
        settings=modified_settings,
    )
    modified_record = SinglePaperAnalysisRecord.from_result(modified_result)
    reopened_storage.save_single_paper_analysis(modified_record)

    reopened_storage.close()

    final_storage = SQLiteStorage(db_path)
    final_storage.initialize()
    replaced_read = final_storage.get_single_paper_analysis(modified_record.analysis_id)
    assert replaced_read is not None
    assert replaced_read.settings.section_settings.max_candidate_search_lines == 150
    final_storage.close()

    return case_summaries


@pytest.mark.real_pdf
def test_pdf_acceptance_harness_opt_in(tmp_path: Path) -> None:
    env_dir = os.getenv(ACCEPTANCE_ENV_VAR)
    if not env_dir:
        pytest.skip(
            f"{ACCEPTANCE_ENV_VAR} is not set. Skipping real PDF acceptance harness."
        )

    acceptance_path = Path(env_dir).resolve()
    if not acceptance_path.exists():
        pytest.fail(f"Acceptance directory does not exist: {acceptance_path}")

    custom_paper_dir = os.getenv(ACCEPTANCE_PAPER_DIR_ENV_VAR)
    papers_dir = (
        Path(custom_paper_dir).resolve()
        if custom_paper_dir
        else (acceptance_path / "papers")
    )

    summaries = run_pdf_acceptance_harness(papers_dir, tmp_path)
    assert all(status == "PASSED" for status in summaries.values())
