"""Immutable domain contracts for deterministic PDF section detection."""

from dataclasses import dataclass
from enum import Enum

from econ_paper_cli.domain.errors import PDFSectionValidationError


class PDFSectionKind(str, Enum):
    """Canonical section kinds supported by deterministic section detection."""

    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"


_DISPLAY_LABELS = {
    PDFSectionKind.ABSTRACT: "Abstract",
    PDFSectionKind.INTRODUCTION: "Introduction",
}


def get_canonical_section_label(kind: PDFSectionKind) -> str:
    """Return the canonical display label for a PDFSectionKind."""
    if not isinstance(kind, PDFSectionKind):
        raise PDFSectionValidationError(
            f"kind must be a PDFSectionKind instance, got {type(kind).__name__}."
        )
    return _DISPLAY_LABELS[kind]


class PDFSectionDetectionMethod(str, Enum):
    """How a section's boundaries were determined."""

    EXPLICIT_HEADING = "explicit_heading"
    IMPLICIT_FRONT_MATTER = "implicit_front_matter"


class PDFSectionBoundaryEvidenceType(str, Enum):
    """Stable identifiers for section boundary evidence cues in closed vocabulary."""

    TITLE_BLOCK = "title_block"
    AUTHOR_BLOCK = "author_block"
    ABSTRACT_HEADING = "abstract_heading"
    INTRODUCTION_HEADING = "introduction_heading"
    FIRST_SECTION_HEADING = "first_section_heading"
    PAGE_BOUNDARY = "page_boundary"
    UNHEADED_PARAGRAPH_BREAK = "unheaded_paragraph_break"
    JEL_CLASSIFICATION_TERMINATOR = "jel_classification_terminator"
    ACKNOWLEDGMENTS_START = "acknowledgments_start"
    FIRST_BODY_PROSE_START = "first_body_prose_start"


class PDFSectionWarningCode(str, Enum):
    """Stable warning identifiers for section detection in canonical output order."""

    NO_PAGES = "no_pages"
    ALL_PAGES_EMPTY = "all_pages_empty"
    MISSING_ABSTRACT = "missing_abstract"
    UNRESOLVED_ABSTRACT_BOUNDARY = "unresolved_abstract_boundary"
    EMPTY_ABSTRACT_BODY = "empty_abstract_body"
    MISSING_INTRODUCTION = "missing_introduction"
    MISSING_NEXT_SECTION_BOUNDARY = "missing_next_section_boundary"
    EMPTY_INTRODUCTION_BODY = "empty_introduction_body"
    DUPLICATE_ABSTRACT_CANDIDATES = "duplicate_abstract_candidates"
    DUPLICATE_INTRODUCTION_CANDIDATES = "duplicate_introduction_candidates"
    AMBIGUOUS_ABSTRACT_CANDIDATES = "ambiguous_abstract_candidates"
    AMBIGUOUS_INTRODUCTION_CANDIDATES = "ambiguous_introduction_candidates"
    AMBIGUOUS_IMPLICIT_ABSTRACT_BOUNDARY = "ambiguous_implicit_abstract_boundary"
    AMBIGUOUS_IMPLICIT_INTRODUCTION_BOUNDARY = (
        "ambiguous_implicit_introduction_boundary"
    )


_WARNING_MESSAGES = {
    PDFSectionWarningCode.NO_PAGES: (
        "The extraction result contains no pages. Section detection requires at least "
        "one page."
    ),
    PDFSectionWarningCode.ALL_PAGES_EMPTY: (
        "No extractable text was found on any page. Inspect document quality before "
        "detecting sections."
    ),
    PDFSectionWarningCode.MISSING_ABSTRACT: (
        "No Abstract heading candidate was detected in the document text."
    ),
    PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY: (
        "Abstract heading candidate was detected, but its end boundary could not be "
        "resolved."
    ),
    PDFSectionWarningCode.EMPTY_ABSTRACT_BODY: (
        "Abstract heading was detected, but the section content is empty."
    ),
    PDFSectionWarningCode.MISSING_INTRODUCTION: (
        "No Introduction heading candidate was detected in the document text."
    ),
    PDFSectionWarningCode.MISSING_NEXT_SECTION_BOUNDARY: (
        "Introduction heading was detected, but no subsequent top-level section "
        "heading was found. Introduction extends to the end of the extracted text."
    ),
    PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY: (
        "Introduction heading was detected, but the section content is empty."
    ),
    PDFSectionWarningCode.DUPLICATE_ABSTRACT_CANDIDATES: (
        "Multiple distinct Abstract heading candidates were detected."
    ),
    PDFSectionWarningCode.DUPLICATE_INTRODUCTION_CANDIDATES: (
        "Multiple distinct Introduction heading candidates were detected."
    ),
    PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES: (
        "Multiple equally plausible Abstract heading candidates were detected."
    ),
    PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES: (
        "Multiple equally plausible Introduction heading candidates were detected."
    ),
    PDFSectionWarningCode.AMBIGUOUS_IMPLICIT_ABSTRACT_BOUNDARY: (
        "No Abstract heading was found and the unheaded front-matter boundary could "
        "not be inferred with sufficient confidence."
    ),
    PDFSectionWarningCode.AMBIGUOUS_IMPLICIT_INTRODUCTION_BOUNDARY: (
        "No Introduction heading was found and the unheaded front-matter boundary "
        "could not be inferred with sufficient confidence."
    ),
}
_WARNING_ORDER = {code: position for position, code in enumerate(PDFSectionWarningCode)}

_CANONICAL_SECTION_SETTINGS: dict[str, dict[str, object]] = {
    "pdf-section-detection-v1": {},
    "pdf-section-detection-v2": {},
    # v3 recovers papers whose Introduction is unheaded: an "Abstract" line
    # appearing after the body starts is no longer selectable, an abstract
    # terminated by an inline "(JEL ...)" is recognized, and an explicit
    # Abstract with no following Introduction heading now yields an
    # Abstract-only section instead of nothing at all.
    "pdf-section-detection-v3": {},
}


@dataclass(frozen=True, slots=True)
class PDFSectionSettings:
    """Versioned and validated configuration for section detection."""

    policy_version: str = "pdf-section-detection-v3"

    def __post_init__(self) -> None:
        _validate_nonempty_text("policy_version", self.policy_version)
        canonical = _CANONICAL_SECTION_SETTINGS.get(self.policy_version)
        if canonical is None:
            raise PDFSectionValidationError(
                f"policy_version '{self.policy_version}' is not a recognized policy version."
            )


@dataclass(frozen=True, slots=True)
class PDFSectionSpan:
    """Character offset span within one source page."""

    page_number: int
    start_character_offset: int
    end_character_offset: int

    def __post_init__(self) -> None:
        _validate_positive_int("page_number", self.page_number)
        _validate_nonnegative_int("start_character_offset", self.start_character_offset)
        _validate_nonnegative_int("end_character_offset", self.end_character_offset)
        if self.start_character_offset > self.end_character_offset:
            raise PDFSectionValidationError(
                "start_character_offset cannot exceed end_character_offset."
            )

    @property
    def character_count(self) -> int:
        """Return the total character length of this span."""
        return self.end_character_offset - self.start_character_offset


@dataclass(frozen=True, slots=True)
class PDFHeadingCandidate:
    """Observed heading candidate location and text for provenance and traceability."""

    kind: PDFSectionKind
    heading_text: str
    page_number: int
    start_character_offset: int
    end_character_offset: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PDFSectionKind):
            raise PDFSectionValidationError("kind must be a PDFSectionKind instance.")
        _validate_nonempty_text("heading_text", self.heading_text)
        _validate_positive_int("page_number", self.page_number)
        _validate_nonnegative_int("start_character_offset", self.start_character_offset)
        _validate_nonnegative_int("end_character_offset", self.end_character_offset)
        if self.start_character_offset > self.end_character_offset:
            raise PDFSectionValidationError(
                "start_character_offset cannot exceed end_character_offset."
            )


@dataclass(frozen=True, slots=True)
class PDFSectionBoundaryEvidence:
    """Page- and character-offset grounded evidence for section boundary inference."""

    page_number: int
    start_character_offset: int
    end_character_offset: int
    evidence_type: PDFSectionBoundaryEvidenceType
    description: str

    def __post_init__(self) -> None:
        _validate_positive_int("page_number", self.page_number)
        _validate_nonnegative_int("start_character_offset", self.start_character_offset)
        _validate_nonnegative_int("end_character_offset", self.end_character_offset)
        if self.start_character_offset > self.end_character_offset:
            raise PDFSectionValidationError(
                "start_character_offset cannot exceed end_character_offset."
            )
        if not isinstance(self.evidence_type, PDFSectionBoundaryEvidenceType):
            if isinstance(self.evidence_type, str):
                try:
                    object.__setattr__(
                        self,
                        "evidence_type",
                        PDFSectionBoundaryEvidenceType(self.evidence_type),
                    )
                except ValueError:
                    raise PDFSectionValidationError(
                        f"evidence_type '{self.evidence_type}' is not a recognized PDFSectionBoundaryEvidenceType."
                    )
            else:
                raise PDFSectionValidationError(
                    "evidence_type must be a PDFSectionBoundaryEvidenceType instance or valid string."
                )
        _validate_nonempty_text("description", self.description)


@dataclass(frozen=True, slots=True)
class PDFSection:
    """Detected section with page span provenance and text.

    ``observed_heading_text`` is the verbatim source heading when
    ``detection_method`` is ``EXPLICIT_HEADING``, and ``None`` when the
    section's boundaries were inferred from front-matter structure without an
    observed heading (``IMPLICIT_FRONT_MATTER``). Canonical display labels are
    derived from ``kind`` via :attr:`display_label`, never fabricated as an
    observed heading. ``boundary_evidence`` is empty for explicit sections and
    required for implicit front-matter sections.
    """

    kind: PDFSectionKind
    detection_method: PDFSectionDetectionMethod
    observed_heading_text: str | None
    start_page_number: int
    end_page_number: int
    spans: tuple[PDFSectionSpan, ...]
    text: str
    boundary_evidence: tuple[PDFSectionBoundaryEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PDFSectionKind):
            raise PDFSectionValidationError("kind must be a PDFSectionKind instance.")
        if not isinstance(self.detection_method, PDFSectionDetectionMethod):
            raise PDFSectionValidationError(
                "detection_method must be a PDFSectionDetectionMethod instance."
            )
        if not isinstance(self.boundary_evidence, tuple) or not all(
            isinstance(ev, PDFSectionBoundaryEvidence) for ev in self.boundary_evidence
        ):
            raise PDFSectionValidationError(
                "boundary_evidence must be a tuple of PDFSectionBoundaryEvidence instances."
            )

        if self.boundary_evidence:
            ev_keys = [
                (
                    ev.page_number,
                    ev.start_character_offset,
                    ev.end_character_offset,
                    ev.evidence_type.value,
                    ev.description,
                )
                for ev in self.boundary_evidence
            ]
            if len(set(ev_keys)) != len(ev_keys):
                raise PDFSectionValidationError(
                    "boundary_evidence items must be unique."
                )
            if ev_keys != sorted(ev_keys):
                raise PDFSectionValidationError(
                    "boundary_evidence items must be ordered deterministically by page_number, start_character_offset, end_character_offset, evidence_type, and description."
                )

        if self.detection_method is PDFSectionDetectionMethod.EXPLICIT_HEADING:
            _validate_nonempty_text("observed_heading_text", self.observed_heading_text)
            if self.boundary_evidence:
                raise PDFSectionValidationError(
                    "boundary_evidence must be empty when detection_method is EXPLICIT_HEADING."
                )
        elif self.observed_heading_text is not None:
            raise PDFSectionValidationError(
                "observed_heading_text must be None when detection_method is "
                "IMPLICIT_FRONT_MATTER."
            )
        else:
            if not self.boundary_evidence:
                raise PDFSectionValidationError(
                    "boundary_evidence is required and cannot be empty when detection_method is IMPLICIT_FRONT_MATTER."
                )
        _validate_positive_int("start_page_number", self.start_page_number)
        _validate_positive_int("end_page_number", self.end_page_number)
        if self.start_page_number > self.end_page_number:
            raise PDFSectionValidationError(
                "start_page_number cannot exceed end_page_number."
            )
        if not isinstance(self.spans, tuple) or not all(
            isinstance(span, PDFSectionSpan) for span in self.spans
        ):
            raise PDFSectionValidationError(
                "spans must be a tuple of PDFSectionSpan instances."
            )
        if not self.spans:
            raise PDFSectionValidationError("spans cannot be empty.")
        if not isinstance(self.text, str) or not self.text.strip():
            raise PDFSectionValidationError("text must be a non-empty string.")

        if self.spans[0].page_number != self.start_page_number:
            raise PDFSectionValidationError(
                "start_page_number must match the first span's page_number."
            )
        if self.spans[-1].page_number != self.end_page_number:
            raise PDFSectionValidationError(
                "end_page_number must match the last span's page_number."
            )

        total_span_chars = sum(span.character_count for span in self.spans)
        if len(self.text) != total_span_chars:
            raise PDFSectionValidationError(
                f"text length ({len(self.text)}) does not match total character count "
                f"of spans ({total_span_chars})."
            )

        previous_page = 0
        previous_end_offset = 0
        for span in self.spans:
            if span.page_number < previous_page:
                raise PDFSectionValidationError(
                    "spans must be ordered by page_number and offset."
                )
            if (
                span.page_number == previous_page
                and span.start_character_offset < previous_end_offset
            ):
                raise PDFSectionValidationError(
                    "spans on the same page cannot overlap or be out of order."
                )
            previous_page = span.page_number
            previous_end_offset = span.end_character_offset

    @property
    def display_label(self) -> str:
        """Return the canonical display label derived from ``kind``."""
        return _DISPLAY_LABELS[self.kind]


@dataclass(frozen=True, slots=True)
class PDFSectionWarning:
    """Actionable section detection warning with affected page numbers."""

    code: PDFSectionWarningCode
    page_numbers: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, PDFSectionWarningCode):
            raise PDFSectionValidationError(
                "code must be a PDFSectionWarningCode instance."
            )
        if not isinstance(self.page_numbers, tuple):
            raise PDFSectionValidationError("page_numbers must be a tuple.")
        for page_number in self.page_numbers:
            _validate_positive_int("page_number", page_number)
        if tuple(sorted(set(self.page_numbers))) != self.page_numbers:
            raise PDFSectionValidationError(
                "page_numbers must be unique and in ascending order."
            )

    @property
    def message(self) -> str:
        """Return the canonical message for this warning code."""
        return _WARNING_MESSAGES[self.code]


@dataclass(frozen=True, slots=True)
class PDFSectionDetectionResult:
    """Result of deterministic PDF section detection."""

    policy_version: str
    sections: tuple[PDFSection, ...]
    candidates: tuple[PDFHeadingCandidate, ...]
    warnings: tuple[PDFSectionWarning, ...]

    def __post_init__(self) -> None:
        _validate_nonempty_text("policy_version", self.policy_version)
        if self.policy_version not in _CANONICAL_SECTION_SETTINGS:
            raise PDFSectionValidationError(
                f"policy_version '{self.policy_version}' is not a recognized policy version."
            )
        if not isinstance(self.sections, tuple) or not all(
            isinstance(section, PDFSection) for section in self.sections
        ):
            raise PDFSectionValidationError(
                "sections must be a tuple of PDFSection instances."
            )
        if not isinstance(self.candidates, tuple) or not all(
            isinstance(candidate, PDFHeadingCandidate) for candidate in self.candidates
        ):
            raise PDFSectionValidationError(
                "candidates must be a tuple of PDFHeadingCandidate instances."
            )
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(warning, PDFSectionWarning) for warning in self.warnings
        ):
            raise PDFSectionValidationError(
                "warnings must be a tuple of PDFSectionWarning instances."
            )

        section_kinds = tuple(section.kind for section in self.sections)
        if len(set(section_kinds)) != len(section_kinds):
            raise PDFSectionValidationError(
                "each section kind must be unique in sections."
            )

        warning_codes = tuple(warning.code for warning in self.warnings)
        if len(set(warning_codes)) != len(warning_codes):
            raise PDFSectionValidationError("warning codes must be unique.")
        if (
            tuple(sorted(warning_codes, key=_WARNING_ORDER.__getitem__))
            != warning_codes
        ):
            raise PDFSectionValidationError("warnings must use canonical code order.")

        warning_code_set = set(warning_codes)
        has_sections = len(self.sections) > 0
        section_kinds_set = set(section_kinds)

        if PDFSectionWarningCode.NO_PAGES in warning_code_set and has_sections:
            raise PDFSectionValidationError(
                "NO_PAGES warning contradicts presence of sections."
            )
        if PDFSectionWarningCode.ALL_PAGES_EMPTY in warning_code_set and has_sections:
            raise PDFSectionValidationError(
                "ALL_PAGES_EMPTY warning contradicts presence of sections."
            )

        if PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES in warning_code_set:
            if PDFSectionKind.ABSTRACT in section_kinds_set:
                raise PDFSectionValidationError(
                    "AMBIGUOUS_ABSTRACT_CANDIDATES warning contradicts presence of Abstract section."
                )

        if (
            PDFSectionWarningCode.AMBIGUOUS_IMPLICIT_ABSTRACT_BOUNDARY
            in warning_code_set
        ):
            if PDFSectionKind.ABSTRACT in section_kinds_set:
                raise PDFSectionValidationError(
                    "AMBIGUOUS_IMPLICIT_ABSTRACT_BOUNDARY warning contradicts presence of Abstract section."
                )
            if PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY in warning_code_set:
                raise PDFSectionValidationError(
                    "AMBIGUOUS_IMPLICIT_ABSTRACT_BOUNDARY warning contradicts UNRESOLVED_ABSTRACT_BOUNDARY warning."
                )
            if PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES in warning_code_set:
                raise PDFSectionValidationError(
                    "AMBIGUOUS_IMPLICIT_ABSTRACT_BOUNDARY warning contradicts AMBIGUOUS_ABSTRACT_CANDIDATES warning."
                )

        if (
            PDFSectionWarningCode.AMBIGUOUS_IMPLICIT_INTRODUCTION_BOUNDARY
            in warning_code_set
        ):
            if PDFSectionKind.INTRODUCTION in section_kinds_set:
                raise PDFSectionValidationError(
                    "AMBIGUOUS_IMPLICIT_INTRODUCTION_BOUNDARY warning contradicts presence of Introduction section."
                )
            if (
                PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES
                in warning_code_set
            ):
                raise PDFSectionValidationError(
                    "AMBIGUOUS_IMPLICIT_INTRODUCTION_BOUNDARY warning contradicts AMBIGUOUS_INTRODUCTION_CANDIDATES warning."
                )

        if PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY in warning_code_set:
            if PDFSectionKind.ABSTRACT in section_kinds_set:
                raise PDFSectionValidationError(
                    "UNRESOLVED_ABSTRACT_BOUNDARY warning contradicts presence of Abstract section."
                )
            if PDFSectionWarningCode.MISSING_ABSTRACT in warning_code_set:
                raise PDFSectionValidationError(
                    "UNRESOLVED_ABSTRACT_BOUNDARY warning contradicts MISSING_ABSTRACT warning."
                )
            if PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES in warning_code_set:
                raise PDFSectionValidationError(
                    "UNRESOLVED_ABSTRACT_BOUNDARY warning contradicts AMBIGUOUS_ABSTRACT_CANDIDATES warning."
                )

        if PDFSectionWarningCode.EMPTY_ABSTRACT_BODY in warning_code_set:
            if PDFSectionKind.ABSTRACT in section_kinds_set:
                raise PDFSectionValidationError(
                    "EMPTY_ABSTRACT_BODY warning contradicts presence of Abstract section."
                )
            if PDFSectionWarningCode.MISSING_ABSTRACT in warning_code_set:
                raise PDFSectionValidationError(
                    "EMPTY_ABSTRACT_BODY warning contradicts MISSING_ABSTRACT warning."
                )
            if PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY in warning_code_set:
                raise PDFSectionValidationError(
                    "EMPTY_ABSTRACT_BODY warning contradicts UNRESOLVED_ABSTRACT_BOUNDARY warning."
                )
            if PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES in warning_code_set:
                raise PDFSectionValidationError(
                    "EMPTY_ABSTRACT_BODY warning contradicts AMBIGUOUS_ABSTRACT_CANDIDATES warning."
                )

        if PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES in warning_code_set:
            if PDFSectionKind.INTRODUCTION in section_kinds_set:
                raise PDFSectionValidationError(
                    "AMBIGUOUS_INTRODUCTION_CANDIDATES warning contradicts presence of Introduction section."
                )

        if PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY in warning_code_set:
            if PDFSectionKind.INTRODUCTION in section_kinds_set:
                raise PDFSectionValidationError(
                    "EMPTY_INTRODUCTION_BODY warning contradicts presence of Introduction section."
                )
            if PDFSectionWarningCode.MISSING_INTRODUCTION in warning_code_set:
                raise PDFSectionValidationError(
                    "EMPTY_INTRODUCTION_BODY warning contradicts MISSING_INTRODUCTION warning."
                )
            if (
                PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES
                in warning_code_set
            ):
                raise PDFSectionValidationError(
                    "EMPTY_INTRODUCTION_BODY warning contradicts AMBIGUOUS_INTRODUCTION_CANDIDATES warning."
                )

        # Grounding checks for candidates
        if len(set(self.candidates)) != len(self.candidates):
            raise PDFSectionValidationError("candidates must be unique.")

        abstract_candidates = tuple(
            c for c in self.candidates if c.kind is PDFSectionKind.ABSTRACT
        )
        intro_candidates = tuple(
            c for c in self.candidates if c.kind is PDFSectionKind.INTRODUCTION
        )

        for warning in self.warnings:
            if warning.code in {
                PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY,
                PDFSectionWarningCode.EMPTY_ABSTRACT_BODY,
            }:
                if not abstract_candidates:
                    raise PDFSectionValidationError(
                        f"{warning.code.value} warning must be grounded by at least 1 Abstract candidate."
                    )
                abstract_pages = set(c.page_number for c in abstract_candidates)
                if not warning.page_numbers or not set(warning.page_numbers).issubset(
                    abstract_pages
                ):
                    raise PDFSectionValidationError(
                        f"{warning.code.value} page_numbers ({warning.page_numbers}) "
                        f"must be a non-empty subset of candidate page numbers ({tuple(sorted(abstract_pages))})."
                    )

            if warning.code is PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY:
                if not intro_candidates:
                    raise PDFSectionValidationError(
                        f"{warning.code.value} warning must be grounded by at least 1 Introduction candidate."
                    )
                intro_pages = set(c.page_number for c in intro_candidates)
                if not warning.page_numbers or not set(warning.page_numbers).issubset(
                    intro_pages
                ):
                    raise PDFSectionValidationError(
                        f"{warning.code.value} page_numbers ({warning.page_numbers}) "
                        f"must be a non-empty subset of candidate page numbers ({tuple(sorted(intro_pages))})."
                    )

            if warning.code in {
                PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES,
                PDFSectionWarningCode.DUPLICATE_ABSTRACT_CANDIDATES,
            }:
                distinct_abstract = set(abstract_candidates)
                if len(distinct_abstract) < 2:
                    raise PDFSectionValidationError(
                        f"{warning.code.value} warning must be grounded by at least 2 distinct Abstract candidates."
                    )
                expected_pages = tuple(
                    sorted(set(c.page_number for c in distinct_abstract))
                )
                if warning.page_numbers != expected_pages:
                    raise PDFSectionValidationError(
                        f"{warning.code.value} page_numbers ({warning.page_numbers}) "
                        f"do not match candidate page numbers ({expected_pages})."
                    )

            if warning.code in {
                PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES,
                PDFSectionWarningCode.DUPLICATE_INTRODUCTION_CANDIDATES,
            }:
                distinct_intro = set(intro_candidates)
                if len(distinct_intro) < 2:
                    raise PDFSectionValidationError(
                        f"{warning.code.value} warning must be grounded by at least 2 distinct Introduction candidates."
                    )
                expected_pages = tuple(
                    sorted(set(c.page_number for c in distinct_intro))
                )
                if warning.page_numbers != expected_pages:
                    raise PDFSectionValidationError(
                        f"{warning.code.value} page_numbers ({warning.page_numbers}) "
                        f"do not match candidate page numbers ({expected_pages})."
                    )

            if warning.code in {
                PDFSectionWarningCode.AMBIGUOUS_IMPLICIT_ABSTRACT_BOUNDARY,
                PDFSectionWarningCode.AMBIGUOUS_IMPLICIT_INTRODUCTION_BOUNDARY,
            }:
                if not warning.page_numbers:
                    raise PDFSectionValidationError(
                        f"{warning.code.value} warning must reference at least 1 page."
                    )

        if PDFSectionKind.ABSTRACT in section_kinds_set:
            if PDFSectionWarningCode.MISSING_ABSTRACT in warning_code_set:
                raise PDFSectionValidationError(
                    "MISSING_ABSTRACT warning contradicts presence of Abstract section."
                )
        elif (
            PDFSectionWarningCode.NO_PAGES not in warning_code_set
            and PDFSectionWarningCode.ALL_PAGES_EMPTY not in warning_code_set
            and PDFSectionWarningCode.AMBIGUOUS_ABSTRACT_CANDIDATES
            not in warning_code_set
            and PDFSectionWarningCode.UNRESOLVED_ABSTRACT_BOUNDARY
            not in warning_code_set
            and PDFSectionWarningCode.EMPTY_ABSTRACT_BODY not in warning_code_set
            and PDFSectionWarningCode.AMBIGUOUS_IMPLICIT_ABSTRACT_BOUNDARY
            not in warning_code_set
        ):
            if PDFSectionWarningCode.MISSING_ABSTRACT not in warning_code_set:
                raise PDFSectionValidationError(
                    "Result missing Abstract section must include MISSING_ABSTRACT warning."
                )

        if PDFSectionKind.INTRODUCTION in section_kinds_set:
            if PDFSectionWarningCode.MISSING_INTRODUCTION in warning_code_set:
                raise PDFSectionValidationError(
                    "MISSING_INTRODUCTION warning contradicts presence of Introduction section."
                )
        elif (
            PDFSectionWarningCode.NO_PAGES not in warning_code_set
            and PDFSectionWarningCode.ALL_PAGES_EMPTY not in warning_code_set
            and PDFSectionWarningCode.AMBIGUOUS_INTRODUCTION_CANDIDATES
            not in warning_code_set
            and PDFSectionWarningCode.EMPTY_INTRODUCTION_BODY not in warning_code_set
            and PDFSectionWarningCode.AMBIGUOUS_IMPLICIT_INTRODUCTION_BOUNDARY
            not in warning_code_set
        ):
            if PDFSectionWarningCode.MISSING_INTRODUCTION not in warning_code_set:
                raise PDFSectionValidationError(
                    "Result missing Introduction section must include MISSING_INTRODUCTION warning."
                )

        if len(self.sections) > 1:
            prev_sec = self.sections[0]
            for curr_sec in self.sections[1:]:
                if curr_sec.start_page_number < prev_sec.end_page_number:
                    raise PDFSectionValidationError(
                        "sections must be ordered and non-overlapping."
                    )
                if (
                    curr_sec.start_page_number == prev_sec.end_page_number
                    and curr_sec.spans[0].start_character_offset
                    < prev_sec.spans[-1].end_character_offset
                ):
                    raise PDFSectionValidationError(
                        "sections on the same page cannot overlap."
                    )
                prev_sec = curr_sec


def _validate_nonempty_text(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PDFSectionValidationError(f"{field_name} must be a non-empty string.")


def _validate_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PDFSectionValidationError(
            f"{field_name} must be a positive integer (>= 1)."
        )


def _validate_nonnegative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PDFSectionValidationError(f"{field_name} must be a non-negative integer.")


DEFAULT_PDF_SECTION_SETTINGS = PDFSectionSettings()
