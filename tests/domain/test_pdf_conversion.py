"""Tests for immutable early-section conversion contracts."""

from dataclasses import FrozenInstanceError

import pytest

from econ_paper_cli.domain import (
    DEFAULT_PDF_CONVERSION_SETTINGS,
    Passage,
    PassageProvenance,
    PassageSourceFragment,
    PDFConversionResult,
    PDFConversionSettings,
    PDFConversionStatus,
    PDFConversionValidationError,
    PDFSectionKind,
    compute_conversion_paper_id,
    compute_conversion_passage_id,
    compute_conversion_settings_fingerprint,
)

CHECKSUM = "a" * 64


def _valid_result() -> PDFConversionResult:
    settings = DEFAULT_PDF_CONVERSION_SETTINGS
    fingerprint = compute_conversion_settings_fingerprint(settings)
    paper_id = compute_conversion_paper_id(CHECKSUM)
    text = "Evidence text."
    passage_id = compute_conversion_passage_id(
        paper_id=paper_id,
        settings_fingerprint=fingerprint,
        section_kind=PDFSectionKind.ABSTRACT,
        ordinal_position=0,
        text=text,
    )
    passage = Passage(
        passage_id=passage_id,
        paper_id=paper_id,
        text=text,
        section_heading="Abstract",
        page_start=1,
        page_end=1,
        ordinal_position=0,
    )
    provenance = PassageProvenance(
        passage_id=passage_id,
        section_kind=PDFSectionKind.ABSTRACT,
        fragments=(
            PassageSourceFragment(
                page_number=1,
                start_character_offset=4,
                end_character_offset=18,
                passage_start_character_offset=0,
                passage_end_character_offset=14,
            ),
        ),
    )
    return PDFConversionResult(
        status=PDFConversionStatus.SUCCESS,
        content_checksum=CHECKSUM,
        settings=settings,
        settings_fingerprint=fingerprint,
        paper_id=paper_id,
        markdown="# Paper\n\n## Abstract\n\nEvidence text.",
        passages=(passage,),
        passage_provenance=(provenance,),
    )


def test_default_settings_and_fingerprint_are_stable() -> None:
    assert DEFAULT_PDF_CONVERSION_SETTINGS.max_passage_characters == 1200
    assert DEFAULT_PDF_CONVERSION_SETTINGS.policy_version == (
        "early-section-markdown-v1"
    )
    assert compute_conversion_settings_fingerprint(
        DEFAULT_PDF_CONVERSION_SETTINGS
    ) == compute_conversion_settings_fingerprint(PDFConversionSettings())
    assert compute_conversion_settings_fingerprint(
        PDFConversionSettings(max_passage_characters=100)
    ) != compute_conversion_settings_fingerprint(DEFAULT_PDF_CONVERSION_SETTINGS)


@pytest.mark.parametrize("budget", [0, -1, True, 1.2, "1200"])
def test_settings_require_positive_integer_budget(budget: object) -> None:
    with pytest.raises(PDFConversionValidationError, match="positive integer"):
        PDFConversionSettings(max_passage_characters=budget)  # type: ignore[arg-type]


def test_settings_reject_unknown_policy_and_are_frozen() -> None:
    with pytest.raises(PDFConversionValidationError, match="not recognized"):
        PDFConversionSettings(policy_version="early-section-markdown-v2")
    with pytest.raises(FrozenInstanceError):
        DEFAULT_PDF_CONVERSION_SETTINGS.max_passage_characters = 99  # type: ignore[misc]


def test_checksum_derived_paper_id_is_canonical() -> None:
    assert compute_conversion_paper_id(CHECKSUM) == f"paper-{CHECKSUM}"
    with pytest.raises(PDFConversionValidationError, match="64-character"):
        compute_conversion_paper_id("A" * 64)


def test_fragment_contract_requires_equal_nonempty_spans() -> None:
    with pytest.raises(PDFConversionValidationError, match="equal lengths"):
        PassageSourceFragment(1, 0, 4, 0, 3)
    with pytest.raises(PDFConversionValidationError, match="non-empty"):
        PassageSourceFragment(1, 0, 0, 0, 0)


def test_provenance_requires_contiguous_ordered_nonoverlapping_fragments() -> None:
    first = PassageSourceFragment(1, 4, 7, 0, 3)
    with pytest.raises(PDFConversionValidationError, match="contiguously"):
        PassageProvenance(
            "passage-a",
            PDFSectionKind.ABSTRACT,
            (first, PassageSourceFragment(1, 7, 9, 4, 6)),
        )
    with pytest.raises(PDFConversionValidationError, match="overlap"):
        PassageProvenance(
            "passage-a",
            PDFSectionKind.ABSTRACT,
            (first, PassageSourceFragment(1, 6, 8, 3, 5)),
        )


def test_success_result_cross_validates_identity_and_provenance() -> None:
    result = _valid_result()
    assert result.status is PDFConversionStatus.SUCCESS
    with pytest.raises(PDFConversionValidationError, match="settings_fingerprint"):
        PDFConversionResult(
            status=result.status,
            content_checksum=result.content_checksum,
            settings=result.settings,
            settings_fingerprint="b" * 64,
            paper_id=result.paper_id,
            markdown=result.markdown,
            passages=result.passages,
            passage_provenance=result.passage_provenance,
        )


def test_no_usable_sections_is_a_typed_empty_terminal_result() -> None:
    settings = DEFAULT_PDF_CONVERSION_SETTINGS
    result = PDFConversionResult(
        status=PDFConversionStatus.NO_USABLE_SECTIONS,
        content_checksum=CHECKSUM,
        settings=settings,
        settings_fingerprint=compute_conversion_settings_fingerprint(settings),
        paper_id=compute_conversion_paper_id(CHECKSUM),
        markdown=None,
        passages=(),
        passage_provenance=(),
    )
    assert result.markdown is None
    with pytest.raises(PDFConversionValidationError, match="cannot contain"):
        PDFConversionResult(
            status=result.status,
            content_checksum=result.content_checksum,
            settings=result.settings,
            settings_fingerprint=result.settings_fingerprint,
            paper_id=result.paper_id,
            markdown="# Invalid",
            passages=(),
            passage_provenance=(),
        )
