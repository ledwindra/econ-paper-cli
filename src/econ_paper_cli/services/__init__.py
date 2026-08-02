"""Application services used by user-facing adapters."""

from econ_paper_cli.services.ingestion import (
    discover_pdf_paths,
    run_ingestion_preflight,
)
from econ_paper_cli.services.pdf_conversion import convert_pdf_early_sections
from econ_paper_cli.services.pdf_extraction import extract_pdf
from econ_paper_cli.services.pdf_quality import assess_pdf_extraction_quality
from econ_paper_cli.services.pdf_section_detection import detect_pdf_sections
from econ_paper_cli.services.research_question_extraction import (
    extract_research_question,
)
from econ_paper_cli.services.single_paper_analysis import (
    analyze_single_paper,
    build_preflight_failure_result,
)
from econ_paper_cli.services.single_paper_analysis_storage import (
    delete_single_paper_analysis_record,
    get_single_paper_analysis_record,
    get_single_paper_analysis_record_by_checksum,
    list_single_paper_analysis_records,
    save_single_paper_analysis_result,
)

__all__ = [
    "run_ingestion_preflight",
    "discover_pdf_paths",
    "extract_pdf",
    "assess_pdf_extraction_quality",
    "convert_pdf_early_sections",
    "detect_pdf_sections",
    "extract_research_question",
    "analyze_single_paper",
    "build_preflight_failure_result",
    "save_single_paper_analysis_result",
    "get_single_paper_analysis_record",
    "get_single_paper_analysis_record_by_checksum",
    "list_single_paper_analysis_records",
    "delete_single_paper_analysis_record",
]
