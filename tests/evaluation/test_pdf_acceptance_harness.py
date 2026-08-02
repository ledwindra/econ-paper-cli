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
    PreflightCandidate,
    SinglePaperAnalysisRecord,
    SinglePaperAnalysisSettings,
    SinglePaperAnalysisStatus,
)
from econ_paper_cli.protocols.generation import (
    AbstentionReason,
    FindingKind,
    GenerationRequest,
    GenerationResponse,
    Generator,
    validate_generation_response,
)
from econ_paper_cli.protocols.retrieval import (
    RetrievalRequest,
    validate_retrieval_results,
)
from econ_paper_cli.services.analysis_library import LibraryPopulationStatus
from econ_paper_cli.services.early_section_library import (
    project_early_section_library_record,
)
from econ_paper_cli.services.pdf_conversion import convert_pdf_early_sections
from econ_paper_cli.services.pdf_section_detection import detect_pdf_sections
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper
from econ_paper_cli.services.single_paper_analysis_cli import (
    BatchOutcomeKind,
    _process_candidate,
)

ACCEPTANCE_ENV_VAR = "ECONPAPERS_TEST_ACCEPTANCE_DIR"
ACCEPTANCE_PAPER_DIR_ENV_VAR = "ECONPAPERS_ACCEPTANCE_PAPER_DIR"


@dataclasses.dataclass(frozen=True, slots=True)
class BenchmarkCaseManifest:
    """One approved acceptance case, identified by exact local filename.

    ``filename`` is the exact basename documented on issue #59, not a glob.
    ``papers/`` holds a large unrelated bulk corpus alongside the six
    approved files, so loose patterns (``*Regional*.pdf``, ``*trade*.pdf``)
    match dozens of unrelated papers and would silently benchmark the wrong
    document. Several approved files also have a ``... (1).pdf`` duplicate
    copy in that directory, which an exact basename resolves unambiguously.
    """

    case_id: str
    filename: str
    expected_abstract_method: PDFSectionDetectionMethod
    expected_intro_method: PDFSectionDetectionMethod


BENCHMARK_MANIFEST = (
    BenchmarkCaseManifest(
        case_id="case_a",
        filename="1-s2.0-S009411902600001X-main.pdf",
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
    ),
    BenchmarkCaseManifest(
        case_id="case_b",
        filename="lbaf056.pdf",
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
    ),
    BenchmarkCaseManifest(
        case_id="case_c",
        filename=(
            "aspelund-russo-2026-additionality-and-asymmetric-information-in-"
            "environmental-markets-evidence-from-conservation.pdf"
        ),
        expected_abstract_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
        expected_intro_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
    ),
    BenchmarkCaseManifest(
        case_id="case_d",
        filename=(
            "Journal of Regional Science - 2026 - Ma - Attracting Top Talent  "
            "Analyzing Local Policies in China With Economists  CV Data.pdf"
        ),
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
    ),
    BenchmarkCaseManifest(
        case_id="case_e",
        filename="Trade  gravity and cross-sectional dependence.pdf",
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
    ),
    BenchmarkCaseManifest(
        case_id="case_f",
        filename="427465.pdf",
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
            # The project's validation boundary derives allowed citation ids
            # from evidence rank ("e1", "e2", ...); a bespoke id would be
            # rejected, so this mock must speak the real contract.
            citation_id=f"e{first_ev.rank}",
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


def resolve_benchmark_files(papers_dir: Path) -> dict[str, Path]:
    """Map each approved case_id to its exact local PDF, or fail hard.

    Resolution is by exact basename and is case-sensitive, so it can never
    silently pick an unrelated paper out of the surrounding bulk corpus.
    A missing file, a path that is not a regular file, or two distinct
    cases resolving to the same file all fail loudly rather than degrading
    into a partial or misattributed benchmark run.
    """
    resolved: dict[str, Path] = {}
    missing: list[str] = []

    for manifest_entry in BENCHMARK_MANIFEST:
        target = papers_dir / manifest_entry.filename
        if not target.is_file():
            missing.append(f"{manifest_entry.case_id}: '{manifest_entry.filename}'")
            continue
        resolved[manifest_entry.case_id] = target

    if missing:
        pytest.fail(
            f"Acceptance corpus in '{papers_dir}' is incomplete; missing "
            f"{len(missing)} approved file(s): {sorted(missing)}."
        )

    by_path: dict[Path, list[str]] = {}
    for case_id, path in resolved.items():
        by_path.setdefault(path.resolve(), []).append(case_id)
    ambiguous = {
        str(path): sorted(case_ids)
        for path, case_ids in by_path.items()
        if len(case_ids) > 1
    }
    if ambiguous:
        pytest.fail(
            f"Acceptance corpus in '{papers_dir}' is ambiguous; one file "
            f"resolves to multiple cases: {ambiguous}."
        )

    assert len(resolved) == len(BENCHMARK_MANIFEST)
    return resolved


def run_pdf_acceptance_harness(papers_dir: Path, db_dir: Path) -> dict[str, str]:
    """Execute full 6-paper benchmark acceptance suite over a papers directory."""
    if not papers_dir.exists():
        pytest.fail(f"Acceptance papers directory does not exist: {papers_dir}")

    matched_files = resolve_benchmark_files(papers_dir)

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
        # Research-question extraction is deterministic and model-free, so a
        # paper whose Abstract/Introduction phrasing yields no extractable
        # question halts at that stage. That is a legitimate terminal
        # outcome for this issue's scope; what must never happen is a
        # preflight or extraction *failure* on an approved corpus file.
        assert analysis_result.status in (
            SinglePaperAnalysisStatus.SUCCESS,
            SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED,
        ), (
            f"[{case_id}] analysis failed before question extraction: "
            f"{analysis_result.status} ({analysis_result.failure_code})"
        )
        assert analysis_result.preflight_result is not None
        assert analysis_result.preflight_result.total_candidate_count == 1
        assert analysis_result.section_result is not None
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

    # 6. BM25 Retrieval & Grounded Response Validation
    #
    # Goes through the project's real retrieval and generation boundary
    # (RetrievalRequest -> Retriever.retrieve -> GenerationRequest ->
    # validate_generation_response), never a bespoke helper, so the
    # citation check exercises the same validation production does.
    retriever = BM25Retriever(corpus)
    retrieval_request = RetrievalRequest(query="economic model analysis", top_k=5)
    evidence = retriever.retrieve(retrieval_request)
    validate_retrieval_results(retrieval_request, evidence)
    assert len(evidence) > 0

    request = GenerationRequest(question="What is the model?", evidence=evidence)
    response = generator.generate(request)
    validate_generation_response(request, response)
    assert not response.abstained
    assert len(response.citations) == 1

    # 7. Production CLI Candidate Reuse & Settings Replacement
    sample_pdf = matched_files["case_a"]
    checksum_sample = hashlib.sha256(sample_pdf.read_bytes()).hexdigest()
    candidate = PreflightCandidate(
        source_path=sample_pdf.resolve(),
        content_checksum=checksum_sample,
        file_size_bytes=sample_pdf.stat().st_size,
        is_stored=True,
        is_batch_duplicate=False,
    )
    reuse_outcome = _process_candidate(
        candidate=candidate,
        pdf_path=sample_pdf.resolve(),
        storage=reopened_storage,
        extractor=extractor,
        generator_provider=lambda: generator,
        analysis_settings=DEFAULT_SINGLE_PAPER_ANALYSIS_SETTINGS,
        conversion_settings=conversion_settings,
        timestamp_provider=lambda: "2026-08-02T12:00:00Z",
    )
    assert reuse_outcome.kind is BatchOutcomeKind.REUSED
    assert reuse_outcome.library_result is not None
    assert reuse_outcome.library_result.status is LibraryPopulationStatus.REUSED, (
        "second pass over an already-ingested paper must hit the production "
        "reuse path, not re-ingest it"
    )

    # Re-analyzing the same paper under a *different* section policy must
    # produce a distinct analysis identity that persists independently,
    # rather than silently colliding with the record stored above.
    modified_settings = SinglePaperAnalysisSettings(
        section_settings=PDFSectionSettings(policy_version="pdf-section-detection-v1")
    )
    modified_result = analyze_single_paper(
        sample_pdf,
        extractor,
        generator,
        settings=modified_settings,
    )
    # settings= is required: without it the record would be built against
    # the default settings and misrepresent the modified analysis identity.
    modified_record = SinglePaperAnalysisRecord.from_result(
        modified_result, settings=modified_settings
    )
    reopened_storage.save_single_paper_analysis(modified_record)

    reopened_storage.close()

    final_storage = SQLiteStorage(db_path)
    final_storage.initialize()
    replaced_read = final_storage.get_single_paper_analysis(modified_record.analysis_id)
    assert replaced_read is not None
    assert replaced_read.settings.section_settings.policy_version == (
        "pdf-section-detection-v1"
    )
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
