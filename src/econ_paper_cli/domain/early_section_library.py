"""Immutable storage contract for converted early PDF sections."""

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from econ_paper_cli.domain.errors import EarlySectionLibraryValidationError
from econ_paper_cli.domain.papers import Paper
from econ_paper_cli.domain.passages import Passage
from econ_paper_cli.domain.pdf_conversion import (
    PassageProvenance,
    PassageSourceFragment,
    PDFConversionResult,
    PDFConversionSettings,
    PDFConversionStatus,
)
from econ_paper_cli.domain.pdf_sections import PDFSectionKind
from econ_paper_cli.domain.storage import SourceProvenance

_SHA256_PATTERN = re.compile(r"[a-f0-9]{64}")
_RECORD_FIELDS = {
    "paper",
    "source_provenance",
    "parser_version",
    "conversion_settings",
    "settings_fingerprint",
    "markdown",
    "markdown_sha256",
    "passages",
    "passage_provenance",
    "created_at",
    "updated_at",
}


@dataclass(frozen=True, slots=True)
class StoredPassageSourceFragment:
    """Persisted source span with enough text for restart-time integrity checks."""

    page_number: int
    start_character_offset: int
    end_character_offset: int
    passage_start_character_offset: int
    passage_end_character_offset: int
    source_text: str

    def __post_init__(self) -> None:
        for field_name, value, minimum in (
            ("page_number", self.page_number, 1),
            ("start_character_offset", self.start_character_offset, 0),
            ("end_character_offset", self.end_character_offset, 0),
            (
                "passage_start_character_offset",
                self.passage_start_character_offset,
                0,
            ),
            ("passage_end_character_offset", self.passage_end_character_offset, 0),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise EarlySectionLibraryValidationError(
                    f"{field_name} must be an integer greater than or equal to {minimum}."
                )
        if self.start_character_offset >= self.end_character_offset:
            raise EarlySectionLibraryValidationError(
                "source fragment offsets must describe a non-empty span."
            )
        if self.passage_start_character_offset >= self.passage_end_character_offset:
            raise EarlySectionLibraryValidationError(
                "passage fragment offsets must describe a non-empty span."
            )
        if not isinstance(self.source_text, str):
            raise EarlySectionLibraryValidationError("source_text must be a string.")
        source_length = self.end_character_offset - self.start_character_offset
        passage_length = (
            self.passage_end_character_offset - self.passage_start_character_offset
        )
        if source_length != passage_length or len(self.source_text) != source_length:
            raise EarlySectionLibraryValidationError(
                "source_text and source/passage fragment spans must have equal lengths."
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "StoredPassageSourceFragment":
        expected = {
            "page_number",
            "start_character_offset",
            "end_character_offset",
            "passage_start_character_offset",
            "passage_end_character_offset",
            "source_text",
        }
        _validate_mapping_fields(data, expected, "StoredPassageSourceFragment")
        return cls(
            page_number=cast(int, data["page_number"]),
            start_character_offset=cast(int, data["start_character_offset"]),
            end_character_offset=cast(int, data["end_character_offset"]),
            passage_start_character_offset=cast(
                int, data["passage_start_character_offset"]
            ),
            passage_end_character_offset=cast(
                int, data["passage_end_character_offset"]
            ),
            source_text=cast(str, data["source_text"]),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "page_number": self.page_number,
            "start_character_offset": self.start_character_offset,
            "end_character_offset": self.end_character_offset,
            "passage_start_character_offset": self.passage_start_character_offset,
            "passage_end_character_offset": self.passage_end_character_offset,
            "source_text": self.source_text,
        }


@dataclass(frozen=True, slots=True)
class StoredPassageProvenance:
    """Persisted ordered provenance for one converted passage."""

    passage_id: str
    section_kind: PDFSectionKind
    fragments: tuple[StoredPassageSourceFragment, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.passage_id, str) or not self.passage_id:
            raise EarlySectionLibraryValidationError("passage_id must be non-empty.")
        if not isinstance(self.section_kind, PDFSectionKind):
            raise EarlySectionLibraryValidationError(
                "section_kind must be a PDFSectionKind instance."
            )
        if not isinstance(self.fragments, tuple) or not self.fragments:
            raise EarlySectionLibraryValidationError(
                "fragments must be a non-empty tuple."
            )
        if not all(
            isinstance(fragment, StoredPassageSourceFragment)
            for fragment in self.fragments
        ):
            raise EarlySectionLibraryValidationError(
                "fragments must contain StoredPassageSourceFragment instances."
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "StoredPassageProvenance":
        """Validate and construct provenance from a mapping."""
        _validate_mapping_fields(
            data,
            {"passage_id", "section_kind", "fragments"},
            "StoredPassageProvenance",
        )
        raw_fragments = data["fragments"]
        if not isinstance(raw_fragments, (list, tuple)):
            raise EarlySectionLibraryValidationError(
                "StoredPassageProvenance.fragments must be a sequence."
            )
        try:
            section_kind = PDFSectionKind(cast(str, data["section_kind"]))
        except (TypeError, ValueError) as error:
            raise EarlySectionLibraryValidationError(
                "section_kind is not recognized."
            ) from error
        return cls(
            passage_id=cast(str, data["passage_id"]),
            section_kind=section_kind,
            fragments=tuple(
                StoredPassageSourceFragment.from_mapping(
                    cast(Mapping[str, object], fragment)
                )
                for fragment in cast(Sequence[object], raw_fragments)
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "passage_id": self.passage_id,
            "section_kind": self.section_kind.value,
            "fragments": [fragment.to_mapping() for fragment in self.fragments],
        }


@dataclass(frozen=True, slots=True)
class EarlySectionLibraryRecord:
    """Complete durable representation of one successful early-section conversion."""

    paper: Paper
    source_provenance: SourceProvenance
    parser_version: str
    conversion_settings: PDFConversionSettings
    settings_fingerprint: str
    markdown: str
    markdown_sha256: str
    passages: tuple[Passage, ...]
    passage_provenance: tuple[StoredPassageProvenance, ...]
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.paper, Paper):
            raise EarlySectionLibraryValidationError("paper must be a Paper instance.")
        if not isinstance(self.source_provenance, SourceProvenance):
            raise EarlySectionLibraryValidationError(
                "source_provenance must be a SourceProvenance instance."
            )
        if self.source_provenance.source_format != "pdf":
            raise EarlySectionLibraryValidationError("source_format must be 'pdf'.")
        if not Path(self.source_provenance.source_path).is_absolute():
            raise EarlySectionLibraryValidationError("source_path must be absolute.")
        if self.source_provenance.markdown_path is not None:
            raise EarlySectionLibraryValidationError(
                "database-backed early-section records cannot have markdown_path."
            )
        for name, value in (
            ("parser_version", self.parser_version),
            ("markdown", self.markdown),
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            if not isinstance(value, str) or not value.strip():
                raise EarlySectionLibraryValidationError(
                    f"{name} must be a non-empty string."
                )
        if not isinstance(self.conversion_settings, PDFConversionSettings):
            raise EarlySectionLibraryValidationError(
                "conversion_settings must be PDFConversionSettings."
            )
        if (
            not isinstance(self.settings_fingerprint, str)
            or _SHA256_PATTERN.fullmatch(self.settings_fingerprint) is None
        ):
            raise EarlySectionLibraryValidationError(
                "settings_fingerprint must be lowercase SHA-256 hex."
            )
        if self.markdown_sha256 != compute_markdown_sha256(self.markdown):
            raise EarlySectionLibraryValidationError(
                "markdown_sha256 does not match the exact Markdown bytes."
            )
        if not isinstance(self.passages, tuple) or not isinstance(
            self.passage_provenance, tuple
        ):
            raise EarlySectionLibraryValidationError(
                "passages and passage_provenance must be tuples."
            )
        if not all(isinstance(passage, Passage) for passage in self.passages):
            raise EarlySectionLibraryValidationError(
                "passages must contain Passage instances."
            )
        if not all(
            isinstance(provenance, StoredPassageProvenance)
            for provenance in self.passage_provenance
        ):
            raise EarlySectionLibraryValidationError(
                "passage_provenance must contain StoredPassageProvenance instances."
            )
        if len(self.passages) != len(self.passage_provenance):
            raise EarlySectionLibraryValidationError(
                "each passage must have exactly one provenance record."
            )

        conversion_provenance = tuple(
            PassageProvenance(
                passage_id=provenance.passage_id,
                section_kind=provenance.section_kind,
                fragments=tuple(
                    PassageSourceFragment(
                        page_number=fragment.page_number,
                        start_character_offset=fragment.start_character_offset,
                        end_character_offset=fragment.end_character_offset,
                        passage_start_character_offset=(
                            fragment.passage_start_character_offset
                        ),
                        passage_end_character_offset=(
                            fragment.passage_end_character_offset
                        ),
                    )
                    for fragment in provenance.fragments
                ),
            )
            for provenance in self.passage_provenance
        )
        try:
            PDFConversionResult(
                status=PDFConversionStatus.SUCCESS,
                content_checksum=self.source_provenance.content_checksum,
                settings=self.conversion_settings,
                settings_fingerprint=self.settings_fingerprint,
                paper_id=self.paper.paper_id,
                markdown=self.markdown,
                passages=self.passages,
                passage_provenance=conversion_provenance,
            )
        except ValueError as error:
            raise EarlySectionLibraryValidationError(
                f"conversion fields are inconsistent: {error}"
            ) from error

        if self.paper.source_name != "local-pdf":
            raise EarlySectionLibraryValidationError("source_name must be 'local-pdf'.")
        if self.paper.source_identifier != self.source_provenance.content_checksum:
            raise EarlySectionLibraryValidationError(
                "source_identifier must match content_checksum."
            )
        if self.source_provenance.created_at != self.created_at:
            raise EarlySectionLibraryValidationError(
                "source provenance created_at must match record created_at."
            )
        for passage, provenance in zip(
            self.passages, self.passage_provenance, strict=True
        ):
            for fragment in provenance.fragments:
                passage_slice = passage.text[
                    fragment.passage_start_character_offset : fragment.passage_end_character_offset
                ]
                if fragment.source_text != passage_slice:
                    raise EarlySectionLibraryValidationError(
                        f"stored source fragment for passage '{passage.passage_id}' "
                        "does not match the corresponding passage slice."
                    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "EarlySectionLibraryRecord":
        """Validate and construct a record from a mapping."""
        _validate_mapping_fields(data, _RECORD_FIELDS, "EarlySectionLibraryRecord")
        raw_passages = data["passages"]
        raw_provenance = data["passage_provenance"]
        if not isinstance(raw_passages, (list, tuple)) or not isinstance(
            raw_provenance, (list, tuple)
        ):
            raise EarlySectionLibraryValidationError(
                "passages and passage_provenance must be sequences."
            )
        raw_settings = data["conversion_settings"]
        if not isinstance(raw_settings, Mapping):
            raise EarlySectionLibraryValidationError(
                "conversion_settings must be a mapping."
            )
        if set(raw_settings) != {"policy_version", "max_passage_characters"}:
            raise EarlySectionLibraryValidationError(
                "conversion_settings fields are invalid."
            )
        return cls(
            paper=Paper.from_mapping(cast(Mapping[str, object], data["paper"])),
            source_provenance=SourceProvenance.from_mapping(
                cast(Mapping[str, object], data["source_provenance"])
            ),
            parser_version=cast(str, data["parser_version"]),
            conversion_settings=PDFConversionSettings(
                policy_version=cast(str, raw_settings["policy_version"]),
                max_passage_characters=cast(
                    int, raw_settings["max_passage_characters"]
                ),
            ),
            settings_fingerprint=cast(str, data["settings_fingerprint"]),
            markdown=cast(str, data["markdown"]),
            markdown_sha256=cast(str, data["markdown_sha256"]),
            passages=tuple(
                Passage.from_mapping(cast(Mapping[str, object], passage))
                for passage in cast(Sequence[object], raw_passages)
            ),
            passage_provenance=tuple(
                StoredPassageProvenance.from_mapping(
                    cast(Mapping[str, object], provenance)
                )
                for provenance in cast(Sequence[object], raw_provenance)
            ),
            created_at=cast(str, data["created_at"]),
            updated_at=cast(str, data["updated_at"]),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "paper": self.paper.to_mapping(),
            "source_provenance": self.source_provenance.to_mapping(),
            "parser_version": self.parser_version,
            "conversion_settings": {
                "policy_version": self.conversion_settings.policy_version,
                "max_passage_characters": self.conversion_settings.max_passage_characters,
            },
            "settings_fingerprint": self.settings_fingerprint,
            "markdown": self.markdown,
            "markdown_sha256": self.markdown_sha256,
            "passages": [passage.to_mapping() for passage in self.passages],
            "passage_provenance": [
                provenance.to_mapping() for provenance in self.passage_provenance
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def compute_markdown_sha256(markdown: str) -> str:
    """Return the SHA-256 digest of exact UTF-8 Markdown bytes."""
    if not isinstance(markdown, str) or not markdown.strip():
        raise EarlySectionLibraryValidationError("markdown must be a non-empty string.")
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _validate_mapping_fields(
    data: Mapping[str, object], expected: set[str], name: str
) -> None:
    if not isinstance(data, Mapping):
        raise EarlySectionLibraryValidationError(f"{name} must be a mapping.")
    provided = set(data)
    missing = sorted(expected - provided)
    unknown = sorted(provided - expected)
    if missing:
        raise EarlySectionLibraryValidationError(
            f"{name} missing fields: {', '.join(missing)}."
        )
    if unknown:
        raise EarlySectionLibraryValidationError(
            f"{name} contains unknown fields: {', '.join(unknown)}."
        )
