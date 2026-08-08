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
    PDFSectionBoundaryEvidenceType,
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
    # Observed heading text, or None where the section is unheaded and must
    # be represented truthfully rather than given a fabricated heading.
    expected_abstract_heading: str | None
    expected_intro_heading: str | None
    # Boundary evidence an implicitly-detected section must be grounded in.
    expected_abstract_evidence: tuple[PDFSectionBoundaryEvidenceType, ...] = ()
    expected_intro_evidence: tuple[PDFSectionBoundaryEvidenceType, ...] = ()
    # The Introduction must stop immediately before this heading. Empty
    # means the paper's next-section boundary is not asserted textually
    # (page furniture immediately follows instead).
    expected_next_section_prefix: str = ""
    # Metadata/furniture/footnote material that must not survive into
    # either retained section for this paper.
    forbidden_substrings: tuple[str, ...] = ()


BENCHMARK_MANIFEST = (
    BenchmarkCaseManifest(
        case_id="case_a",
        filename="1-s2.0-S009411902600001X-main.pdf",
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_abstract_heading="ABSTRACT",
        expected_intro_heading="1. Introduction",
        expected_next_section_prefix="2. The theoretical setup",
        forbidden_substrings=("JEL classification", "Keywords:", "ARTICLE INFO"),
    ),
    BenchmarkCaseManifest(
        case_id="case_b",
        filename="lbaf056.pdf",
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_abstract_heading="Abstract",
        expected_intro_heading="1. Introduction",
        forbidden_substrings=(
            "Keywords:",
            "JEL classification",
            "Downloaded from",
            "academic.oup.com",
        ),
    ),
    BenchmarkCaseManifest(
        case_id="case_c",
        filename=(
            "aspelund-russo-2026-additionality-and-asymmetric-information-in-"
            "environmental-markets-evidence-from-conservation.pdf"
        ),
        expected_abstract_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
        expected_intro_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
        expected_abstract_heading=None,
        expected_intro_heading=None,
        expected_abstract_evidence=(
            PDFSectionBoundaryEvidenceType.TITLE_BLOCK,
            PDFSectionBoundaryEvidenceType.JEL_CLASSIFICATION_TERMINATOR,
        ),
        expected_intro_evidence=(PDFSectionBoundaryEvidenceType.FIRST_SECTION_HEADING,),
        expected_next_section_prefix="I. Theoretical Framework",
        # Interleaved author-affiliation/acknowledgments footnote.
        forbidden_substrings=(
            "JEL\u00a0D44",
            "We thank",
            "We are grateful",
            "Yale School",
            "karl.aspelund",
            "@",
        ),
    ),
    BenchmarkCaseManifest(
        case_id="case_d",
        filename=(
            "Journal of Regional Science - 2026 - Ma - Attracting Top Talent  "
            "Analyzing Local Policies in China With Economists  CV Data.pdf"
        ),
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_abstract_heading="ABSTRACT",
        expected_intro_heading="1 | Introduction",
        expected_next_section_prefix="2 | Related Literature",
        forbidden_substrings=(
            "JEL Classification",
            "Keywords:",
            "Downloaded from",
            "onlinelibrary.wiley",
        ),
    ),
    BenchmarkCaseManifest(
        case_id="case_e",
        filename="Trade  gravity and cross-sectional dependence.pdf",
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_abstract_heading="ABSTRACT",
        expected_intro_heading="1. Introduction",
        expected_next_section_prefix=(
            "2 Gravity estimation framework and spatial components"
        ),
        forbidden_substrings=(
            "ARTICLE HISTORY",
            "KEYWORDS",
            "Downloaded by",
            "tandfonline",
        ),
    ),
    BenchmarkCaseManifest(
        case_id="case_f",
        filename="427465.pdf",
        expected_abstract_method=PDFSectionDetectionMethod.IMPLICIT_FRONT_MATTER,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_abstract_heading=None,
        expected_intro_heading="I. Introduction",
        expected_abstract_evidence=(
            PDFSectionBoundaryEvidenceType.TITLE_BLOCK,
            PDFSectionBoundaryEvidenceType.ACKNOWLEDGMENTS_START,
        ),
        expected_next_section_prefix="II. Spatial Equilibrium and a Framework",
        forbidden_substrings=("We thank", "JSTOR", "This content downloaded"),
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


def resolve_benchmark_files(
    papers_dir: Path,
    manifest: tuple[BenchmarkCaseManifest, ...] = BENCHMARK_MANIFEST,
) -> dict[str, Path]:
    """Map each approved case_id to its exact local PDF, or fail hard.

    Resolution is by exact basename and is case-sensitive, so it can never
    silently pick an unrelated paper out of the surrounding bulk corpus.
    A missing file, a path that is not a regular file, or two distinct
    cases resolving to the same file all fail loudly rather than degrading
    into a partial or misattributed benchmark run.
    """
    resolved: dict[str, Path] = {}
    missing: list[str] = []

    for manifest_entry in manifest:
        target = papers_dir / manifest_entry.filename
        if not target.is_file():
            missing.append(f"{manifest_entry.case_id}: '{manifest_entry.filename}'")
            continue
        resolved[manifest_entry.case_id] = target

    if missing:
        raise AssertionError(
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
        raise AssertionError(
            f"Acceptance corpus in '{papers_dir}' is ambiguous; one file "
            f"resolves to multiple cases: {ambiguous}."
        )

    assert len(resolved) == len(manifest)
    return resolved


def run_pdf_acceptance_harness(
    papers_dir: Path,
    db_dir: Path,
    *,
    manifest: tuple[BenchmarkCaseManifest, ...] = BENCHMARK_MANIFEST,
    extractor: object | None = None,
) -> dict[str, str]:
    """Execute the benchmark acceptance suite over a papers directory.

    ``manifest`` and ``extractor`` are injectable purely so ordinary CI can
    execute this same orchestration against synthetic inputs. The real
    private-corpus run uses the defaults.
    """
    if not papers_dir.exists():
        raise AssertionError(
            f"Acceptance papers directory does not exist: {papers_dir}"
        )

    matched_files = resolve_benchmark_files(papers_dir, manifest)

    if extractor is None:
        extractor = PyPDFExtractor()
    generator = DeterministicMockGenerator()
    db_path = db_dir / "acceptance_library.sqlite3"
    storage = SQLiteStorage(db_path)
    storage.initialize()

    case_summaries: dict[str, str] = {}

    for manifest_entry in manifest:
        case_id = manifest_entry.case_id
        pdf_path = matched_files[case_id]
        pdf_bytes = pdf_path.read_bytes()
        checksum = hashlib.sha256(pdf_bytes).hexdigest()

        # Each paper's contract failures are collected rather than raised,
        # so one bad paper still yields a full per-paper report instead of
        # aborting the run at the first case.
        failures: list[str] = []

        def check(condition: bool, message: str) -> bool:
            if not condition:
                failures.append(message)
            return condition

        # 1. Extraction
        extraction = extractor.extract(pdf_path)
        check(extraction.page_count > 0, "extraction produced 0 pages")

        # 2. Section detection and the per-case structural contract
        detection = detect_pdf_sections(
            extraction, settings=DEFAULT_PDF_SECTION_SETTINGS
        )
        abs_sec = next(
            (s for s in detection.sections if s.kind is PDFSectionKind.ABSTRACT), None
        )
        intro_sec = next(
            (s for s in detection.sections if s.kind is PDFSectionKind.INTRODUCTION),
            None,
        )
        check(abs_sec is not None, "missing ABSTRACT")
        check(intro_sec is not None, "missing INTRODUCTION")

        # Ambiguity warnings mean detection could not resolve a boundary; an
        # approved corpus paper must resolve cleanly.
        unresolved = [
            warning.code.value
            for warning in detection.warnings
            if "ambiguous" in warning.code.value or "unresolved" in warning.code.value
        ]
        check(not unresolved, f"unresolved/ambiguous boundaries: {unresolved}")

        if abs_sec is not None and intro_sec is not None:
            check(
                abs_sec.detection_method == manifest_entry.expected_abstract_method,
                f"abstract method {abs_sec.detection_method} != "
                f"{manifest_entry.expected_abstract_method}",
            )
            check(
                intro_sec.detection_method == manifest_entry.expected_intro_method,
                f"introduction method {intro_sec.detection_method} != "
                f"{manifest_entry.expected_intro_method}",
            )
            check(
                abs_sec.observed_heading_text
                == manifest_entry.expected_abstract_heading,
                f"abstract heading {abs_sec.observed_heading_text!r} != "
                f"{manifest_entry.expected_abstract_heading!r}",
            )
            check(
                intro_sec.observed_heading_text
                == manifest_entry.expected_intro_heading,
                f"introduction heading {intro_sec.observed_heading_text!r} != "
                f"{manifest_entry.expected_intro_heading!r}",
            )

            for label, section, expected_evidence in (
                ("abstract", abs_sec, manifest_entry.expected_abstract_evidence),
                ("introduction", intro_sec, manifest_entry.expected_intro_evidence),
            ):
                observed_evidence = tuple(
                    item.evidence_type for item in section.boundary_evidence
                )
                check(
                    observed_evidence == expected_evidence,
                    f"{label} boundary evidence {observed_evidence} != "
                    f"{expected_evidence}",
                )

            # Disjoint source spans must reconstruct the retained text
            # exactly, even where excluded furniture sits between them.
            for label, section in (("abstract", abs_sec), ("introduction", intro_sec)):
                reconstructed = "".join(
                    extraction.pages[span.page_number - 1].text[
                        span.start_character_offset : span.end_character_offset
                    ]
                    for span in section.spans
                )
                check(
                    section.text == reconstructed,
                    f"{label} text does not reconstruct from its source spans",
                )

            # The Introduction must stop immediately before the paper's
            # next top-level heading.
            if manifest_entry.expected_next_section_prefix:
                last_span = intro_sec.spans[-1]
                following = extraction.pages[last_span.page_number - 1].text[
                    last_span.end_character_offset :
                ]
                check(
                    following.lstrip().startswith(
                        manifest_entry.expected_next_section_prefix
                    ),
                    "introduction does not terminate immediately before "
                    f"{manifest_entry.expected_next_section_prefix!r}; "
                    f"found {following.lstrip()[:60]!r}",
                )

            # Known metadata/furniture/footnote material must be excluded.
            retained = abs_sec.text + intro_sec.text
            for forbidden in manifest_entry.forbidden_substrings:
                check(
                    forbidden not in retained,
                    f"excluded material {forbidden!r} survived into retained text",
                )

        # 3. Conversion & Ingestion
        conversion_settings = PDFConversionSettings(
            section_policy_version="pdf-section-detection-v3"
        )
        conversion = convert_pdf_early_sections(
            extraction,
            detection,
            content_checksum=checksum,
            settings=conversion_settings,
        )
        check(conversion.markdown is not None, "conversion produced no Markdown")

        if conversion.markdown is not None:
            # Markdown must carry only the two canonical section headings.
            markdown_headings = [
                line.strip()
                for line in conversion.markdown.splitlines()
                if line.startswith("## ")
            ]
            check(
                markdown_headings == ["## Abstract", "## Introduction"],
                f"Markdown headings {markdown_headings} are not exactly "
                "['## Abstract', '## Introduction']",
            )
            for forbidden in manifest_entry.forbidden_substrings:
                check(
                    forbidden not in conversion.markdown,
                    f"excluded material {forbidden!r} survived into Markdown",
                )

        early_record = project_early_section_library_record(
            extraction,
            detection,
            conversion,
            source_file_size=pdf_path.stat().st_size,
            timestamp="2026-08-02T12:00:00Z",
        )
        storage.save_early_section_record(early_record)

        # Persisted fragment provenance must quote its own source exactly.
        check(
            len(early_record.passage_provenance) == len(early_record.passages),
            "provenance count does not match passage count",
        )
        for provenance, passage in zip(
            early_record.passage_provenance, early_record.passages, strict=False
        ):
            quoted = "".join(fragment.source_text for fragment in provenance.fragments)
            check(
                quoted == passage.text,
                f"stored provenance for '{passage.passage_id}' does not "
                "reconstruct its passage text",
            )

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
        check(
            analysis_result.status
            in (
                SinglePaperAnalysisStatus.SUCCESS,
                SinglePaperAnalysisStatus.QUESTION_EXTRACTION_HALTED,
            ),
            f"analysis failed before question extraction: "
            f"{analysis_result.status} ({analysis_result.failure_code})",
        )
        check(analysis_result.preflight_result is not None, "no preflight result")
        if analysis_result.preflight_result is not None:
            check(
                analysis_result.preflight_result.total_candidate_count == 1,
                "preflight did not see exactly one candidate",
            )
        check(analysis_result.section_result is not None, "no section result")
        analysis_record = SinglePaperAnalysisRecord.from_result(analysis_result)
        storage.save_single_paper_analysis(analysis_record)

        case_summaries[case_id] = (
            "PASSED" if not failures else "FAILED: " + "; ".join(failures)
        )

    # 5. Database Close & Reopen Verification
    storage.close()

    reopened_storage = SQLiteStorage(db_path)
    reopened_storage.initialize()

    corpus = reopened_storage.load_corpus()
    assert len(corpus.papers) == len(manifest)
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
    sample_pdf = matched_files[manifest[0].case_id]
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

    failed = {
        case_id: summary
        for case_id, summary in case_summaries.items()
        if summary != "PASSED"
    }
    if failed:
        report = "\n".join(
            f"  {case_id}: {summary}" for case_id, summary in sorted(failed.items())
        )
        raise AssertionError(
            f"{len(failed)} of {len(case_summaries)} acceptance papers failed "
            f"their case contract:\n{report}"
        )

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


# --- Ordinary-CI contract test -------------------------------------------
#
# The real-corpus test above skips without the private PDFs, so on its own it
# proves nothing in normal CI. This exercises the exact same
# run_pdf_acceptance_harness() orchestration — including every production API
# it calls (detection, conversion, projection, SQLite save/reopen,
# load_corpus, BM25 retrieve + validate_retrieval_results, GenerationRequest +
# validate_generation_response, _process_candidate reuse, and analysis
# replacement) — against synthetic pages, so an API drift breaks CI instead of
# lying dormant until someone runs the private suite.


_SYNTHETIC_PAGE = (
    "Synthetic Paper On Regional Markets\n"
    "By A Researcher\n"
    "\n"
    "Abstract\n"
    "This paper studies how synthetic regional markets allocate scarce "
    "resources across competing firms under uncertainty.\n"
    "Keywords: markets, firms\n"
    "\n"
    "1. Introduction\n"
    "We examine the allocation question in detail, building a tractable model "
    "of firm entry and comparing it against observed outcomes.\n"
    "\n"
    "2. Model\n"
    "This content belongs to the next section and must not be retained.\n"
)

SYNTHETIC_MANIFEST = tuple(
    BenchmarkCaseManifest(
        case_id=f"synthetic_{index}",
        filename=f"synthetic_{index}.pdf",
        expected_abstract_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_intro_method=PDFSectionDetectionMethod.EXPLICIT_HEADING,
        expected_abstract_heading="Abstract",
        expected_intro_heading="1. Introduction",
        expected_next_section_prefix="2. Model",
        forbidden_substrings=("Keywords:",),
    )
    for index in range(2)
)


class _SyntheticExtractor:
    """Returns deterministic synthetic pages for any path handed to it."""

    def extract(self, source_path: Path):
        from econ_paper_cli.domain import (
            ExtractedPDFPage,
            PDFDocumentMetadata,
            PDFExtractionResult,
        )

        return PDFExtractionResult(
            source_path=source_path.resolve(),
            pages=(ExtractedPDFPage(1, _SYNTHETIC_PAGE),),
            page_count=1,
            metadata=PDFDocumentMetadata(title="Synthetic", author_text="A Researcher"),
            extraction_method="synthetic-parser",
            parser_version="1.2.3",
        )


def test_acceptance_harness_orchestration_runs_in_ordinary_ci(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    for entry in SYNTHETIC_MANIFEST:
        # Distinct bytes per file so each gets its own checksum identity.
        (papers_dir / entry.filename).write_bytes(
            f"%PDF-1.4 synthetic {entry.case_id}".encode()
        )

    summaries = run_pdf_acceptance_harness(
        papers_dir,
        tmp_path / "db",
        manifest=SYNTHETIC_MANIFEST,
        extractor=_SyntheticExtractor(),
    )

    assert set(summaries) == {entry.case_id for entry in SYNTHETIC_MANIFEST}
    assert all(status == "PASSED" for status in summaries.values())


def test_acceptance_harness_reports_every_paper_not_just_the_first_failure(
    tmp_path: Path,
) -> None:
    """A contract violation must surface as a per-paper report, not abort the
    run at the first bad paper."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    broken_manifest = tuple(
        dataclasses.replace(entry, expected_intro_heading="1. Wrong Heading")
        for entry in SYNTHETIC_MANIFEST
    )
    for entry in broken_manifest:
        (papers_dir / entry.filename).write_bytes(
            f"%PDF-1.4 synthetic {entry.case_id}".encode()
        )

    with pytest.raises(AssertionError, match="2 of 2 acceptance papers failed"):
        run_pdf_acceptance_harness(
            papers_dir,
            tmp_path / "db",
            manifest=broken_manifest,
            extractor=_SyntheticExtractor(),
        )


def test_acceptance_manifest_resolution_fails_on_missing_corpus_file(
    tmp_path: Path,
) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    (papers_dir / SYNTHETIC_MANIFEST[0].filename).write_bytes(b"%PDF-1.4 x")

    with pytest.raises(AssertionError, match="incomplete"):
        resolve_benchmark_files(papers_dir, SYNTHETIC_MANIFEST)
