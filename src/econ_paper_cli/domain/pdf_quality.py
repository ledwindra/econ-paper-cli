"""Immutable domain contracts for deterministic PDF extraction quality."""

from dataclasses import dataclass
from enum import Enum
from math import isfinite

from econ_paper_cli.domain.errors import PDFQualityValidationError


class PDFQualityStatus(str, Enum):
    """Document-level outcome for downstream ingestion orchestration."""

    USABLE = "usable"
    USABLE_WITH_WARNINGS = "usable_with_warnings"
    LIKELY_NEEDS_OCR = "likely_needs_ocr"
    UNUSABLE = "unusable"


class PDFQualityWarningCode(str, Enum):
    """Stable warning identifiers in canonical output order."""

    NO_PAGES = "no_pages"
    ALL_PAGES_EMPTY = "all_pages_empty"
    EMPTY_PAGES = "empty_pages"
    HIGH_EMPTY_PAGE_RATIO = "high_empty_page_ratio"
    VERY_LOW_TEXT_VOLUME = "very_low_text_volume"
    SPARSE_PAGES = "sparse_pages"
    CONTROL_CHARACTERS = "control_characters"
    REPLACEMENT_CHARACTERS = "replacement_characters"
    REPEATED_CHARACTERS = "repeated_characters"
    SEVERE_PAGE_IMBALANCE = "severe_page_imbalance"
    EXTRACTION_GARBAGE = "extraction_garbage"


_WARNING_MESSAGES = {
    PDFQualityWarningCode.NO_PAGES: (
        "The extraction result contains no pages. Inspect the PDF and parser output "
        "before continuing."
    ),
    PDFQualityWarningCode.ALL_PAGES_EMPTY: (
        "No extractable text was found on any page. Run local OCR or inspect the "
        "document manually."
    ),
    PDFQualityWarningCode.EMPTY_PAGES: (
        "Some pages contain no extractable text. Inspect the listed pages before "
        "continuing."
    ),
    PDFQualityWarningCode.HIGH_EMPTY_PAGE_RATIO: (
        "A high share of pages contains no extractable text. The document likely "
        "requires local OCR or manual inspection."
    ),
    PDFQualityWarningCode.VERY_LOW_TEXT_VOLUME: (
        "The document contains very little extracted text. Confirm that extraction "
        "captured the intended content."
    ),
    PDFQualityWarningCode.SPARSE_PAGES: (
        "Some non-empty pages contain unusually little text. Inspect the listed pages "
        "for extraction problems."
    ),
    PDFQualityWarningCode.CONTROL_CHARACTERS: (
        "Suspicious control characters occur in the extracted text. Inspect the "
        "listed pages for parser output corruption."
    ),
    PDFQualityWarningCode.REPLACEMENT_CHARACTERS: (
        "Unicode replacement characters occur in the extracted text. Inspect the "
        "listed pages for decoding or extraction damage."
    ),
    PDFQualityWarningCode.REPEATED_CHARACTERS: (
        "Long repeated-character runs occur in the extracted text. Inspect the listed "
        "pages for extraction garbage."
    ),
    PDFQualityWarningCode.SEVERE_PAGE_IMBALANCE: (
        "Extracted text is severely concentrated on one page. Inspect page coverage "
        "before continuing."
    ),
    PDFQualityWarningCode.EXTRACTION_GARBAGE: (
        "Anomalous characters make the extraction unsafe for downstream conversion. "
        "Inspect the document manually before continuing."
    ),
}
_WARNING_ORDER = {code: position for position, code in enumerate(PDFQualityWarningCode)}


_KNOWN_POLICY_SETTINGS: dict[str, dict[str, object]] = {}


def _settings_thresholds(settings: "PDFQualitySettings") -> dict[str, object]:
    return {
        "sparse_page_non_whitespace_threshold": settings.sparse_page_non_whitespace_threshold,
        "very_low_text_non_whitespace_threshold": settings.very_low_text_non_whitespace_threshold,
        "high_empty_page_ratio_threshold": settings.high_empty_page_ratio_threshold,
        "anomaly_ratio_warning_threshold": settings.anomaly_ratio_warning_threshold,
        "anomaly_ratio_unusable_threshold": settings.anomaly_ratio_unusable_threshold,
        "repeated_character_run_threshold": settings.repeated_character_run_threshold,
        "repeated_character_ratio_unusable_threshold": settings.repeated_character_ratio_unusable_threshold,
        "minimum_pages_for_imbalance": settings.minimum_pages_for_imbalance,
        "severe_page_imbalance_ratio_threshold": settings.severe_page_imbalance_ratio_threshold,
    }


@dataclass(frozen=True, slots=True)
class PDFQualitySettings:
    """Versioned and validated thresholds for one assessment policy."""

    policy_version: str = "pdf-extraction-quality-v1"
    sparse_page_non_whitespace_threshold: int = 80
    very_low_text_non_whitespace_threshold: int = 200
    high_empty_page_ratio_threshold: float = 0.5
    anomaly_ratio_warning_threshold: float = 0.01
    anomaly_ratio_unusable_threshold: float = 0.1
    repeated_character_run_threshold: int = 12
    repeated_character_ratio_unusable_threshold: float = 0.2
    minimum_pages_for_imbalance: int = 4
    severe_page_imbalance_ratio_threshold: float = 0.8

    def __post_init__(self) -> None:
        _validate_nonempty_text("policy_version", self.policy_version)
        for field_name, value in (
            (
                "sparse_page_non_whitespace_threshold",
                self.sparse_page_non_whitespace_threshold,
            ),
            (
                "very_low_text_non_whitespace_threshold",
                self.very_low_text_non_whitespace_threshold,
            ),
            ("repeated_character_run_threshold", self.repeated_character_run_threshold),
        ):
            _validate_positive_int(field_name, value)
        _validate_positive_int(
            "minimum_pages_for_imbalance", self.minimum_pages_for_imbalance
        )
        if self.minimum_pages_for_imbalance < 2:
            raise PDFQualityValidationError(
                "minimum_pages_for_imbalance must be at least 2."
            )
        for field_name, value in (
            ("high_empty_page_ratio_threshold", self.high_empty_page_ratio_threshold),
            ("anomaly_ratio_warning_threshold", self.anomaly_ratio_warning_threshold),
            (
                "anomaly_ratio_unusable_threshold",
                self.anomaly_ratio_unusable_threshold,
            ),
            (
                "repeated_character_ratio_unusable_threshold",
                self.repeated_character_ratio_unusable_threshold,
            ),
            (
                "severe_page_imbalance_ratio_threshold",
                self.severe_page_imbalance_ratio_threshold,
            ),
        ):
            _validate_ratio(field_name, value)
        if (
            self.anomaly_ratio_warning_threshold
            >= self.anomaly_ratio_unusable_threshold
        ):
            raise PDFQualityValidationError(
                "anomaly_ratio_warning_threshold must be less than "
                "anomaly_ratio_unusable_threshold."
            )

        current_thresholds = _settings_thresholds(self)
        existing = _KNOWN_POLICY_SETTINGS.get(self.policy_version)
        if existing is not None:
            if existing != current_thresholds:
                raise PDFQualityValidationError(
                    f"policy_version '{self.policy_version}' is already bound to a "
                    "different threshold set."
                )
        else:
            _KNOWN_POLICY_SETTINGS[self.policy_version] = current_thresholds


@dataclass(frozen=True, slots=True)
class PDFPageQualityObservation:
    """Deterministic measurements for one source page."""

    page_number: int
    character_count: int
    printable_character_count: int
    non_whitespace_character_count: int
    control_character_count: int
    replacement_character_count: int
    repeated_character_count: int
    is_empty: bool
    is_sparse: bool

    def __post_init__(self) -> None:
        _validate_positive_int("page_number", self.page_number)
        for field_name, value in (
            ("character_count", self.character_count),
            ("printable_character_count", self.printable_character_count),
            ("non_whitespace_character_count", self.non_whitespace_character_count),
            ("control_character_count", self.control_character_count),
            ("replacement_character_count", self.replacement_character_count),
            ("repeated_character_count", self.repeated_character_count),
        ):
            _validate_nonnegative_int(field_name, value)
        for field_name, value in (
            ("printable_character_count", self.printable_character_count),
            ("non_whitespace_character_count", self.non_whitespace_character_count),
        ):
            if value > self.character_count:
                raise PDFQualityValidationError(
                    f"{field_name} cannot exceed character_count."
                )
        for field_name, value in (
            ("control_character_count", self.control_character_count),
            ("replacement_character_count", self.replacement_character_count),
            ("repeated_character_count", self.repeated_character_count),
        ):
            if value > self.non_whitespace_character_count:
                raise PDFQualityValidationError(
                    f"{field_name} cannot exceed non_whitespace_character_count."
                )
        if not isinstance(self.is_empty, bool):
            raise PDFQualityValidationError("is_empty must be a boolean.")
        if self.is_empty != (self.non_whitespace_character_count == 0):
            raise PDFQualityValidationError(
                "is_empty must agree with non_whitespace_character_count."
            )
        if not isinstance(self.is_sparse, bool):
            raise PDFQualityValidationError("is_sparse must be a boolean.")
        if self.is_empty and self.is_sparse:
            raise PDFQualityValidationError("an empty page cannot also be sparse.")


@dataclass(frozen=True, slots=True)
class PDFQualityMeasurements:
    """Document-level integer measurements with exact derived ratios."""

    page_count: int
    total_character_count: int
    printable_character_count: int
    non_whitespace_character_count: int
    empty_page_count: int
    sparse_page_count: int
    control_character_count: int
    replacement_character_count: int
    repeated_character_count: int
    minimum_page_non_whitespace_character_count: int
    maximum_page_non_whitespace_character_count: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("page_count", self.page_count),
            ("total_character_count", self.total_character_count),
            ("printable_character_count", self.printable_character_count),
            ("non_whitespace_character_count", self.non_whitespace_character_count),
            ("empty_page_count", self.empty_page_count),
            ("sparse_page_count", self.sparse_page_count),
            ("control_character_count", self.control_character_count),
            ("replacement_character_count", self.replacement_character_count),
            ("repeated_character_count", self.repeated_character_count),
            (
                "minimum_page_non_whitespace_character_count",
                self.minimum_page_non_whitespace_character_count,
            ),
            (
                "maximum_page_non_whitespace_character_count",
                self.maximum_page_non_whitespace_character_count,
            ),
        ):
            _validate_nonnegative_int(field_name, value)
        if self.empty_page_count > self.page_count:
            raise PDFQualityValidationError(
                "empty_page_count cannot exceed page_count."
            )
        if self.sparse_page_count > self.page_count - self.empty_page_count:
            raise PDFQualityValidationError(
                "sparse_page_count cannot exceed the number of non-empty pages."
            )
        for field_name, value in (
            ("printable_character_count", self.printable_character_count),
            ("non_whitespace_character_count", self.non_whitespace_character_count),
        ):
            if value > self.total_character_count:
                raise PDFQualityValidationError(
                    f"{field_name} cannot exceed total_character_count."
                )
        for field_name, value in (
            ("control_character_count", self.control_character_count),
            ("replacement_character_count", self.replacement_character_count),
            ("repeated_character_count", self.repeated_character_count),
        ):
            if value > self.non_whitespace_character_count:
                raise PDFQualityValidationError(
                    f"{field_name} cannot exceed non_whitespace_character_count."
                )
        if (
            self.minimum_page_non_whitespace_character_count
            > self.maximum_page_non_whitespace_character_count
        ):
            raise PDFQualityValidationError(
                "minimum page text count cannot exceed maximum page text count."
            )
        if self.page_count == 0 and any(
            (
                self.total_character_count,
                self.printable_character_count,
                self.non_whitespace_character_count,
                self.empty_page_count,
                self.sparse_page_count,
                self.control_character_count,
                self.replacement_character_count,
                self.repeated_character_count,
                self.minimum_page_non_whitespace_character_count,
                self.maximum_page_non_whitespace_character_count,
            )
        ):
            raise PDFQualityValidationError(
                "all measurements must be zero when page_count is zero."
            )
        if (
            self.minimum_page_non_whitespace_character_count
            > self.maximum_page_non_whitespace_character_count
        ):
            raise PDFQualityValidationError(
                "minimum page text count cannot exceed maximum page text count."
            )
        if self.page_count == 0 and any(
            (
                self.total_character_count,
                self.printable_character_count,
                self.non_whitespace_character_count,
                self.empty_page_count,
                self.sparse_page_count,
                self.control_character_count,
                self.replacement_character_count,
                self.repeated_character_count,
                self.minimum_page_non_whitespace_character_count,
                self.maximum_page_non_whitespace_character_count,
            )
        ):
            raise PDFQualityValidationError(
                "all measurements must be zero when page_count is zero."
            )

    @property
    def empty_page_ratio(self) -> float:
        return _ratio(self.empty_page_count, self.page_count)

    @property
    def sparse_page_ratio(self) -> float:
        return _ratio(self.sparse_page_count, self.page_count)

    @property
    def control_character_ratio(self) -> float:
        return _ratio(self.control_character_count, self.total_character_count)

    @property
    def replacement_character_ratio(self) -> float:
        return _ratio(self.replacement_character_count, self.total_character_count)

    @property
    def repeated_character_ratio(self) -> float:
        return _ratio(
            self.repeated_character_count, self.non_whitespace_character_count
        )

    @property
    def maximum_page_text_ratio(self) -> float:
        return _ratio(
            self.maximum_page_non_whitespace_character_count,
            self.non_whitespace_character_count,
        )


@dataclass(frozen=True, slots=True)
class PDFQualityWarning:
    """A stable warning code, actionable message, and ordered affected pages."""

    code: PDFQualityWarningCode
    page_numbers: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, PDFQualityWarningCode):
            raise PDFQualityValidationError(
                "code must be a PDFQualityWarningCode instance."
            )
        if not isinstance(self.page_numbers, tuple):
            raise PDFQualityValidationError("page_numbers must be a tuple.")
        for page_number in self.page_numbers:
            _validate_positive_int("page_number", page_number)
        if tuple(sorted(set(self.page_numbers))) != self.page_numbers:
            raise PDFQualityValidationError(
                "page_numbers must be unique and in ascending source order."
            )

    @property
    def message(self) -> str:
        """Return the canonical actionable message for this warning code."""
        return _WARNING_MESSAGES[self.code]


@dataclass(frozen=True, slots=True)
class PDFExtractionQualityAssessment:
    """Validated quality decision for one successful extraction result."""

    policy_version: str
    status: PDFQualityStatus
    measurements: PDFQualityMeasurements
    pages: tuple[PDFPageQualityObservation, ...]
    warnings: tuple[PDFQualityWarning, ...]

    def __post_init__(self) -> None:
        _validate_nonempty_text("policy_version", self.policy_version)
        if not isinstance(self.status, PDFQualityStatus):
            raise PDFQualityValidationError(
                "status must be a PDFQualityStatus instance."
            )
        if not isinstance(self.measurements, PDFQualityMeasurements):
            raise PDFQualityValidationError(
                "measurements must be a PDFQualityMeasurements instance."
            )
        if not isinstance(self.pages, tuple) or not all(
            isinstance(page, PDFPageQualityObservation) for page in self.pages
        ):
            raise PDFQualityValidationError(
                "pages must be a tuple of PDFPageQualityObservation instances."
            )
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(warning, PDFQualityWarning) for warning in self.warnings
        ):
            raise PDFQualityValidationError(
                "warnings must be a tuple of PDFQualityWarning instances."
            )
        self._validate_pages_and_measurements()
        self._validate_warnings_and_status()

    def _validate_pages_and_measurements(self) -> None:
        if len(self.pages) != self.measurements.page_count:
            raise PDFQualityValidationError(
                "measurements.page_count must match the number of page observations."
            )
        expected_numbers = tuple(range(1, len(self.pages) + 1))
        if tuple(page.page_number for page in self.pages) != expected_numbers:
            raise PDFQualityValidationError(
                "page observations must use contiguous 1-based source order."
            )
        expected_values = {
            "total_character_count": sum(page.character_count for page in self.pages),
            "printable_character_count": sum(
                page.printable_character_count for page in self.pages
            ),
            "non_whitespace_character_count": sum(
                page.non_whitespace_character_count for page in self.pages
            ),
            "empty_page_count": sum(page.is_empty for page in self.pages),
            "sparse_page_count": sum(page.is_sparse for page in self.pages),
            "control_character_count": sum(
                page.control_character_count for page in self.pages
            ),
            "replacement_character_count": sum(
                page.replacement_character_count for page in self.pages
            ),
            "repeated_character_count": sum(
                page.repeated_character_count for page in self.pages
            ),
            "minimum_page_non_whitespace_character_count": min(
                (page.non_whitespace_character_count for page in self.pages),
                default=0,
            ),
            "maximum_page_non_whitespace_character_count": max(
                (page.non_whitespace_character_count for page in self.pages),
                default=0,
            ),
        }
        for field_name, expected in expected_values.items():
            if getattr(self.measurements, field_name) != expected:
                raise PDFQualityValidationError(
                    f"measurements.{field_name} does not match page observations."
                )

    def _validate_warnings_and_status(self) -> None:
        codes = tuple(warning.code for warning in self.warnings)
        if len(set(codes)) != len(codes):
            raise PDFQualityValidationError("warning codes must be unique.")
        if tuple(sorted(codes, key=_WARNING_ORDER.__getitem__)) != codes:
            raise PDFQualityValidationError("warnings must use canonical code order.")
        valid_pages = set(range(1, self.measurements.page_count + 1))
        for warning in self.warnings:
            if not set(warning.page_numbers).issubset(valid_pages):
                raise PDFQualityValidationError(
                    "warning page_numbers must refer to observed source pages."
                )
        expected_status = _status_for_warning_codes(codes)
        if self.status is not expected_status:
            raise PDFQualityValidationError(
                "status contradicts the assessment warning state."
            )


def _status_for_warning_codes(
    codes: tuple[PDFQualityWarningCode, ...],
) -> PDFQualityStatus:
    code_set = set(codes)
    if code_set & {
        PDFQualityWarningCode.NO_PAGES,
        PDFQualityWarningCode.EXTRACTION_GARBAGE,
    }:
        return PDFQualityStatus.UNUSABLE
    if code_set & {
        PDFQualityWarningCode.ALL_PAGES_EMPTY,
        PDFQualityWarningCode.HIGH_EMPTY_PAGE_RATIO,
    }:
        return PDFQualityStatus.LIKELY_NEEDS_OCR
    if codes:
        return PDFQualityStatus.USABLE_WITH_WARNINGS
    return PDFQualityStatus.USABLE


def _validate_nonempty_text(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PDFQualityValidationError(f"{field_name} must be a non-empty string.")


def _validate_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PDFQualityValidationError(
            f"{field_name} must be a positive integer (>= 1)."
        )


def _validate_nonnegative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PDFQualityValidationError(f"{field_name} must be a non-negative integer.")


def _validate_ratio(field_name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
        or value > 1
    ):
        raise PDFQualityValidationError(
            f"{field_name} must be a finite number greater than 0 and at most 1."
        )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


DEFAULT_PDF_QUALITY_SETTINGS = PDFQualitySettings()
