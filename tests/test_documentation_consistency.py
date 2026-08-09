"""Mechanical guards for documentation claims with a canonical code source."""

import re
from pathlib import Path

from econ_paper_cli.domain.pdf_sections import PDFSectionWarningCode
from econ_paper_cli.domain.research_question import ResearchQuestionWarningCode

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _read_repository_file(relative_path: str) -> str:
    return (_REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def _documented_codes(text: str, *, start: str, end: str) -> tuple[str, ...]:
    section = text.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]
    return tuple(re.findall(r"^- `([^`]+)`:", section, flags=re.MULTILINE))


def test_pdf_section_warning_inventory_matches_domain_enum() -> None:
    text = _read_repository_file("docs/pdf-section-detection.md")

    documented = _documented_codes(
        text,
        start="## Warnings",
        end="## Policy versions",
    )

    assert documented == tuple(code.value for code in PDFSectionWarningCode)


def test_research_question_warning_inventory_matches_domain_enum() -> None:
    text = _read_repository_file("docs/research-question-extraction.md")

    documented = _documented_codes(
        text,
        start="The stable warning codes are:",
        end="## Grounding boundary",
    )

    assert documented == tuple(code.value for code in ResearchQuestionWarningCode)


def test_documented_python_floor_matches_package_metadata() -> None:
    pyproject = _read_repository_file("pyproject.toml")
    match = re.search(r'^requires-python = ">=([^\"]+)"$', pyproject, re.MULTILINE)
    assert match is not None
    floor = match.group(1)

    assert f"Python {floor} or newer" in _read_repository_file(
        "docs/product-requirements.md"
    )
    assert f"Python {floor}+" in _read_repository_file("docs/roadmap.md")


def test_release_checklist_template_version_matches_document_version() -> None:
    text = _read_repository_file("docs/release-checklist.md")
    header = re.search(r"\*\*Checklist version: (\d+)\*\*", text)
    template = re.search(
        r"^Checklist version:\s+(\d+)$",
        text.split("## Run record template", maxsplit=1)[1],
        flags=re.MULTILINE,
    )

    assert header is not None
    assert template is not None
    assert template.group(1) == header.group(1)
