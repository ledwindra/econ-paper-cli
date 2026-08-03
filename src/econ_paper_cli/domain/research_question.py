"""Immutable domain contracts for structured research-question extraction."""

from dataclasses import dataclass
from enum import Enum

from econ_paper_cli.domain.errors import ResearchQuestionValidationError
from econ_paper_cli.domain.pdf_sections import PDFSectionKind


class ResearchQuestionKind(str, Enum):
    """Classification of research question availability and style."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNAVAILABLE = "unavailable"


class ResearchQuestionWarningCode(str, Enum):
    """Stable warning identifiers for research question extraction in canonical order."""

    NO_USABLE_SECTIONS = "no_usable_sections"
    MISSING_SECTION = "missing_section"
    GENERATION_FAILED = "generation_failed"
    MODEL_ABSTAINED = "model_abstained"
    MALFORMED_STRUCTURED_RESPONSE = "malformed_structured_response"
    UNGROUNDED_EVIDENCE = "ungrounded_evidence"


_WARNING_MESSAGES = {
    ResearchQuestionWarningCode.NO_USABLE_SECTIONS: (
        "Neither Abstract nor Introduction is completed and usable. Research question "
        "extraction was skipped."
    ),
    ResearchQuestionWarningCode.MISSING_SECTION: (
        "Only one completed section was available for research question extraction."
    ),
    ResearchQuestionWarningCode.GENERATION_FAILED: (
        "Model generation failed during research question extraction."
    ),
    ResearchQuestionWarningCode.MODEL_ABSTAINED: (
        "Model abstained from generating a research question response due to insufficient evidence."
    ),
    ResearchQuestionWarningCode.MALFORMED_STRUCTURED_RESPONSE: (
        "Model response was malformed or did not conform to the expected structured format."
    ),
    ResearchQuestionWarningCode.UNGROUNDED_EVIDENCE: (
        "Model output contained evidence that could not be grounded in supplied section text."
    ),
}
_WARNING_ORDER = {
    code: position for position, code in enumerate(ResearchQuestionWarningCode)
}

_UNAVAILABLE_WARNING_CODES = frozenset(
    {
        ResearchQuestionWarningCode.NO_USABLE_SECTIONS,
        ResearchQuestionWarningCode.GENERATION_FAILED,
        ResearchQuestionWarningCode.MODEL_ABSTAINED,
        ResearchQuestionWarningCode.MALFORMED_STRUCTURED_RESPONSE,
        ResearchQuestionWarningCode.UNGROUNDED_EVIDENCE,
    }
)

_CANONICAL_RESEARCH_QUESTION_SETTINGS: dict[str, dict[str, object]] = {
    # v1 asked the model to emit a nested JSON object carrying its own
    # excerpt text and exact page character offsets. A local model cannot
    # produce that reliably — the offsets must satisfy
    # `len(excerpt_text) == end - start` against the real page text — and in
    # practice it failed on every paper of a real 268-paper corpus.
    "research-question-extraction-v1": {},
    # v2 asks the model only for the question sentence and which detected
    # section it came from, then derives every traceable offset
    # deterministically from the already-validated section spans. The model
    # can no longer invent provenance it has no way to know.
    "research-question-extraction-v2": {},
}


@dataclass(frozen=True, slots=True)
class ResearchQuestionSettings:
    """Versioned configuration for research-question extraction."""

    policy_version: str = "research-question-extraction-v2"

    def __post_init__(self) -> None:
        _validate_nonempty_text("policy_version", self.policy_version)
        canonical = _CANONICAL_RESEARCH_QUESTION_SETTINGS.get(self.policy_version)
        if canonical is None:
            raise ResearchQuestionValidationError(
                f"policy_version '{self.policy_version}' is not a recognized policy version."
            )


@dataclass(frozen=True, slots=True)
class ResearchQuestionWarning:
    """Actionable warning for research question extraction."""

    code: ResearchQuestionWarningCode
    details: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ResearchQuestionWarningCode):
            raise ResearchQuestionValidationError(
                "code must be a ResearchQuestionWarningCode instance."
            )
        if self.details is not None:
            if not isinstance(self.details, str) or not self.details.strip():
                raise ResearchQuestionValidationError(
                    "details must be a non-empty string or None."
                )

    @property
    def message(self) -> str:
        """Return the canonical message for this warning code."""
        base = _WARNING_MESSAGES[self.code]
        if self.details:
            return f"{base} Details: {self.details}"
        return base


@dataclass(frozen=True, slots=True)
class ResearchQuestionEvidence:
    """Traceable evidence excerpt supporting an extracted research question."""

    section_kind: PDFSectionKind
    excerpt_text: str
    page_number: int
    start_character_offset: int
    end_character_offset: int

    def __post_init__(self) -> None:
        if not isinstance(self.section_kind, PDFSectionKind):
            raise ResearchQuestionValidationError(
                "section_kind must be a PDFSectionKind instance."
            )
        _validate_nonempty_text("excerpt_text", self.excerpt_text)
        _validate_positive_int("page_number", self.page_number)
        _validate_nonnegative_int("start_character_offset", self.start_character_offset)
        _validate_nonnegative_int("end_character_offset", self.end_character_offset)
        if self.start_character_offset > self.end_character_offset:
            raise ResearchQuestionValidationError(
                "start_character_offset cannot exceed end_character_offset."
            )
        expected_len = self.end_character_offset - self.start_character_offset
        if len(self.excerpt_text) != expected_len:
            raise ResearchQuestionValidationError(
                f"excerpt_text length ({len(self.excerpt_text)}) does not match "
                f"character offset span ({expected_len})."
            )


@dataclass(frozen=True, slots=True)
class ResearchQuestionResult:
    """Immutable result of structured research-question extraction."""

    policy_version: str
    question_text: str | None
    kind: ResearchQuestionKind
    sections_used: tuple[PDFSectionKind, ...]
    evidence: tuple[ResearchQuestionEvidence, ...]
    warnings: tuple[ResearchQuestionWarning, ...]

    def __post_init__(self) -> None:
        _validate_nonempty_text("policy_version", self.policy_version)
        if self.policy_version not in _CANONICAL_RESEARCH_QUESTION_SETTINGS:
            raise ResearchQuestionValidationError(
                f"policy_version '{self.policy_version}' is not a recognized policy version."
            )
        if not isinstance(self.kind, ResearchQuestionKind):
            raise ResearchQuestionValidationError(
                "kind must be a ResearchQuestionKind instance."
            )
        if not isinstance(self.sections_used, tuple) or not all(
            isinstance(kind, PDFSectionKind) for kind in self.sections_used
        ):
            raise ResearchQuestionValidationError(
                "sections_used must be a tuple of PDFSectionKind instances."
            )
        if len(set(self.sections_used)) != len(self.sections_used):
            raise ResearchQuestionValidationError(
                "sections_used must not contain duplicates."
            )
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, ResearchQuestionEvidence) for item in self.evidence
        ):
            raise ResearchQuestionValidationError(
                "evidence must be a tuple of ResearchQuestionEvidence instances."
            )
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(warning, ResearchQuestionWarning) for warning in self.warnings
        ):
            raise ResearchQuestionValidationError(
                "warnings must be a tuple of ResearchQuestionWarning instances."
            )

        warning_codes = tuple(w.code for w in self.warnings)
        if len(set(warning_codes)) != len(warning_codes):
            raise ResearchQuestionValidationError("warning codes must be unique.")
        if (
            tuple(sorted(warning_codes, key=_WARNING_ORDER.__getitem__))
            != warning_codes
        ):
            raise ResearchQuestionValidationError(
                "warnings must use canonical code order."
            )

        warning_code_set = set(warning_codes)

        if self.kind is ResearchQuestionKind.UNAVAILABLE:
            if self.question_text is not None:
                raise ResearchQuestionValidationError(
                    "question_text must be None when kind is UNAVAILABLE."
                )
            if self.evidence:
                raise ResearchQuestionValidationError(
                    "evidence must be empty when kind is UNAVAILABLE."
                )
            if not any(code in _UNAVAILABLE_WARNING_CODES for code in warning_code_set):
                raise ResearchQuestionValidationError(
                    "Result with UNAVAILABLE kind must contain at least one terminal warning code."
                )
        else:
            if (
                not isinstance(self.question_text, str)
                or not self.question_text.strip()
            ):
                raise ResearchQuestionValidationError(
                    "question_text must be a non-empty string when question is available."
                )
            if not self.sections_used:
                raise ResearchQuestionValidationError(
                    "sections_used cannot be empty when question is available."
                )
            if not self.evidence:
                raise ResearchQuestionValidationError(
                    "evidence cannot be empty when question is available."
                )
            if any(code in _UNAVAILABLE_WARNING_CODES for code in warning_code_set):
                raise ResearchQuestionValidationError(
                    "Result with available research question cannot contain terminal warning codes."
                )

            evidence_kinds = set(item.section_kind for item in self.evidence)
            if set(self.sections_used) != evidence_kinds:
                raise ResearchQuestionValidationError(
                    "sections_used must exactly match the distinct section kinds present in evidence."
                )


def _validate_nonempty_text(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ResearchQuestionValidationError(
            f"{field_name} must be a non-empty string."
        )


def _validate_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ResearchQuestionValidationError(
            f"{field_name} must be a positive integer (>= 1)."
        )


def _validate_nonnegative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchQuestionValidationError(
            f"{field_name} must be a non-negative integer."
        )


DEFAULT_RESEARCH_QUESTION_SETTINGS = ResearchQuestionSettings()
