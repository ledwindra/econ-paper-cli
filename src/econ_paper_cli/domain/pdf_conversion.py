"""Immutable contracts for deterministic early-section PDF conversion."""

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum

from econ_paper_cli.domain.errors import PDFConversionValidationError
from econ_paper_cli.domain.passages import Passage
from econ_paper_cli.domain.pdf_sections import PDFSectionKind

_SHA256_PATTERN = re.compile(r"[a-f0-9]{64}")
_POLICY_VERSION = "early-section-markdown-v1"


class PDFConversionStatus(str, Enum):
    """Terminal outcome of early-section conversion."""

    SUCCESS = "success"
    NO_USABLE_SECTIONS = "no_usable_sections"


@dataclass(frozen=True, slots=True)
class PDFConversionSettings:
    """Versioned settings for Markdown rendering and passage segmentation."""

    policy_version: str = _POLICY_VERSION
    max_passage_characters: int = 1200

    def __post_init__(self) -> None:
        if self.policy_version != _POLICY_VERSION:
            raise PDFConversionValidationError(
                f"policy_version '{self.policy_version}' is not recognized."
            )
        if (
            isinstance(self.max_passage_characters, bool)
            or not isinstance(self.max_passage_characters, int)
            or self.max_passage_characters < 1
        ):
            raise PDFConversionValidationError(
                "max_passage_characters must be a positive integer."
            )


@dataclass(frozen=True, slots=True)
class PassageSourceFragment:
    """Exact page-local source span contributing to one passage."""

    page_number: int
    start_character_offset: int
    end_character_offset: int
    passage_start_character_offset: int
    passage_end_character_offset: int

    def __post_init__(self) -> None:
        _validate_positive_int("page_number", self.page_number)
        _validate_nonnegative_int("start_character_offset", self.start_character_offset)
        _validate_nonnegative_int("end_character_offset", self.end_character_offset)
        _validate_nonnegative_int(
            "passage_start_character_offset", self.passage_start_character_offset
        )
        _validate_nonnegative_int(
            "passage_end_character_offset", self.passage_end_character_offset
        )
        if self.start_character_offset >= self.end_character_offset:
            raise PDFConversionValidationError(
                "source fragment character offsets must describe a non-empty span."
            )
        if self.passage_start_character_offset >= self.passage_end_character_offset:
            raise PDFConversionValidationError(
                "passage fragment character offsets must describe a non-empty span."
            )
        if self.end_character_offset - self.start_character_offset != (
            self.passage_end_character_offset - self.passage_start_character_offset
        ):
            raise PDFConversionValidationError(
                "source and passage fragment spans must have equal lengths."
            )


@dataclass(frozen=True, slots=True)
class PassageProvenance:
    """Ordered source fragments that collectively account for one passage."""

    passage_id: str
    section_kind: PDFSectionKind
    fragments: tuple[PassageSourceFragment, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.passage_id, str) or not self.passage_id:
            raise PDFConversionValidationError("passage_id must be non-empty.")
        if not isinstance(self.section_kind, PDFSectionKind):
            raise PDFConversionValidationError(
                "section_kind must be a PDFSectionKind instance."
            )
        if not isinstance(self.fragments, tuple) or not all(
            isinstance(fragment, PassageSourceFragment) for fragment in self.fragments
        ):
            raise PDFConversionValidationError(
                "fragments must be a tuple of PassageSourceFragment instances."
            )
        if not self.fragments:
            raise PDFConversionValidationError("fragments cannot be empty.")

        expected_passage_offset = 0
        previous_page = 0
        previous_source_end = 0
        for fragment in self.fragments:
            if fragment.passage_start_character_offset != expected_passage_offset:
                raise PDFConversionValidationError(
                    "fragments must account for passage text contiguously from offset 0."
                )
            if fragment.page_number < previous_page:
                raise PDFConversionValidationError(
                    "fragments must be ordered by source page and character offset."
                )
            if (
                fragment.page_number == previous_page
                and fragment.start_character_offset < previous_source_end
            ):
                raise PDFConversionValidationError(
                    "source fragments cannot overlap or be out of order."
                )
            expected_passage_offset = fragment.passage_end_character_offset
            previous_page = fragment.page_number
            previous_source_end = fragment.end_character_offset


@dataclass(frozen=True, slots=True)
class PDFConversionResult:
    """Deterministic Markdown, passages, and exact passage provenance."""

    status: PDFConversionStatus
    content_checksum: str
    settings: PDFConversionSettings
    settings_fingerprint: str
    paper_id: str
    markdown: str | None
    passages: tuple[Passage, ...]
    passage_provenance: tuple[PassageProvenance, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, PDFConversionStatus):
            raise PDFConversionValidationError(
                "status must be a PDFConversionStatus instance."
            )
        _validate_checksum(self.content_checksum)
        if not isinstance(self.settings, PDFConversionSettings):
            raise PDFConversionValidationError(
                "settings must be a PDFConversionSettings instance."
            )
        expected_fingerprint = compute_conversion_settings_fingerprint(self.settings)
        if self.settings_fingerprint != expected_fingerprint:
            raise PDFConversionValidationError(
                "settings_fingerprint does not match settings."
            )
        if self.paper_id != compute_conversion_paper_id(self.content_checksum):
            raise PDFConversionValidationError(
                "paper_id does not match content_checksum."
            )
        if not isinstance(self.passages, tuple) or not all(
            isinstance(passage, Passage) for passage in self.passages
        ):
            raise PDFConversionValidationError(
                "passages must be a tuple of Passage instances."
            )
        if not isinstance(self.passage_provenance, tuple) or not all(
            isinstance(provenance, PassageProvenance)
            for provenance in self.passage_provenance
        ):
            raise PDFConversionValidationError(
                "passage_provenance must be a tuple of PassageProvenance instances."
            )

        if self.status is PDFConversionStatus.NO_USABLE_SECTIONS:
            if self.markdown is not None or self.passages or self.passage_provenance:
                raise PDFConversionValidationError(
                    "no_usable_sections results cannot contain Markdown or passages."
                )
            return

        if not isinstance(self.markdown, str) or not self.markdown.strip():
            raise PDFConversionValidationError(
                "successful conversion requires non-empty Markdown."
            )
        if not self.passages:
            raise PDFConversionValidationError(
                "successful conversion requires at least one passage."
            )
        if len(self.passages) != len(self.passage_provenance):
            raise PDFConversionValidationError(
                "each passage must have exactly one provenance record."
            )

        passage_ids = tuple(passage.passage_id for passage in self.passages)
        if len(set(passage_ids)) != len(passage_ids):
            raise PDFConversionValidationError("passage IDs must be unique.")
        if tuple(passage.ordinal_position for passage in self.passages) != tuple(
            range(len(self.passages))
        ):
            raise PDFConversionValidationError(
                "passage ordinals must be contiguous and zero-based."
            )

        for passage, provenance in zip(
            self.passages, self.passage_provenance, strict=True
        ):
            if passage.paper_id != self.paper_id:
                raise PDFConversionValidationError(
                    "every passage paper_id must match the conversion paper_id."
                )
            if len(passage.text) > self.settings.max_passage_characters:
                raise PDFConversionValidationError(
                    "passage text cannot exceed max_passage_characters."
                )
            if provenance.passage_id != passage.passage_id:
                raise PDFConversionValidationError(
                    "passage provenance must align with passage order and identity."
                )
            expected_heading = provenance.section_kind.value.title()
            if passage.section_heading != expected_heading:
                raise PDFConversionValidationError(
                    "passage section_heading must match its provenance section kind."
                )
            if provenance.fragments[-1].passage_end_character_offset != len(
                passage.text
            ):
                raise PDFConversionValidationError(
                    "provenance fragments must collectively account for passage text."
                )
            if passage.page_start != provenance.fragments[0].page_number:
                raise PDFConversionValidationError(
                    "page_start must be derived from the first provenance fragment."
                )
            if passage.page_end != provenance.fragments[-1].page_number:
                raise PDFConversionValidationError(
                    "page_end must be derived from the last provenance fragment."
                )
            expected_id = compute_conversion_passage_id(
                paper_id=self.paper_id,
                settings_fingerprint=self.settings_fingerprint,
                section_kind=provenance.section_kind,
                ordinal_position=passage.ordinal_position,
                text=passage.text,
            )
            if passage.passage_id != expected_id:
                raise PDFConversionValidationError(
                    "passage_id does not match its deterministic identity inputs."
                )


DEFAULT_PDF_CONVERSION_SETTINGS = PDFConversionSettings()


def compute_conversion_settings_fingerprint(settings: PDFConversionSettings) -> str:
    """Return the stable SHA-256 fingerprint of conversion settings."""
    if not isinstance(settings, PDFConversionSettings):
        raise TypeError("settings must be a PDFConversionSettings instance.")
    canonical = json.dumps(
        {
            "max_passage_characters": settings.max_passage_characters,
            "policy_version": settings.policy_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_conversion_paper_id(content_checksum: str) -> str:
    """Return the path-independent paper identity derived from PDF bytes."""
    _validate_checksum(content_checksum)
    return f"paper-{content_checksum}"


def compute_conversion_passage_id(
    *,
    paper_id: str,
    settings_fingerprint: str,
    section_kind: PDFSectionKind,
    ordinal_position: int,
    text: str,
) -> str:
    """Return a stable passage identity from all required identity inputs."""
    if not isinstance(paper_id, str) or not paper_id:
        raise PDFConversionValidationError("paper_id must be non-empty.")
    _validate_checksum(settings_fingerprint)
    if not isinstance(section_kind, PDFSectionKind):
        raise PDFConversionValidationError(
            "section_kind must be a PDFSectionKind instance."
        )
    _validate_nonnegative_int("ordinal_position", ordinal_position)
    if not isinstance(text, str) or not text.strip():
        raise PDFConversionValidationError("text must be non-empty.")
    canonical = json.dumps(
        {
            "ordinal_position": ordinal_position,
            "paper_id": paper_id,
            "section_kind": section_kind.value,
            "settings_fingerprint": settings_fingerprint,
            "text": text,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"passage-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _validate_checksum(value: object) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise PDFConversionValidationError(
            "content_checksum must be a 64-character lowercase hexadecimal string."
        )


def _validate_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PDFConversionValidationError(f"{field_name} must be a positive integer.")


def _validate_nonnegative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PDFConversionValidationError(
            f"{field_name} must be a non-negative integer."
        )
