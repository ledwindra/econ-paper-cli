"""Application services used by user-facing adapters."""

from econ_paper_cli.services.ingestion import run_ingestion_preflight
from econ_paper_cli.services.pdf_extraction import extract_pdf
from econ_paper_cli.services.pdf_quality import assess_pdf_extraction_quality
from econ_paper_cli.services.pdf_section_detection import detect_pdf_sections
from econ_paper_cli.services.research_question_extraction import (
    extract_research_question,
)
from econ_paper_cli.services.single_paper_analysis import analyze_single_paper

__all__ = [
    "run_ingestion_preflight",
    "extract_pdf",
    "assess_pdf_extraction_quality",
    "detect_pdf_sections",
    "extract_research_question",
    "analyze_single_paper",
]
