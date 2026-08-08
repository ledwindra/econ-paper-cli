"""Pure domain contracts and pinned catalog for managed GGUF model artifacts.

Mirrors the managed-runtime manifest, minus everything a single-file download
does not need: no archive format, no extraction, and no platform/architecture
selection, because one GGUF runs unchanged on every platform the pinned
``llama.cpp`` release supports.

Every pin here was verified against the live remote before being written down:
the recorded ``sha256`` is Hugging Face's LFS object hash for that exact URL,
cross-checked against a locally computed digest of the downloaded file. A wrong
pin would make provisioning fail for every user, so pins must never be added
from documentation alone.

No filesystem or network access here; see ``services.model_provisioning`` and
``adapters.runtime_downloader`` for that.
"""

import re
from dataclasses import dataclass

from econ_paper_cli.domain.errors import DomainError

_MODEL_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_FILENAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.gguf")


class ManagedModelManifestError(DomainError):
    """Raised when managed-model manifest data violates the schema."""


@dataclass(frozen=True, slots=True)
class ManagedModelArtifact:
    """Validated pinned metadata for one downloadable GGUF model."""

    model_id: str
    display_name: str
    source_url: str
    size_bytes: int
    sha256: str
    filename: str
    license_name: str
    attribution_text: str
    # Shown to a user choosing between sizes. Honest about the tradeoff rather
    # than marketing: the small model is the default because it runs anywhere,
    # not because it answers better.
    summary: str
    minimum_free_ram_bytes: int

    def __post_init__(self) -> None:
        """Validate every pinned field before it can be used for a download."""
        if (
            not isinstance(self.model_id, str)
            or _MODEL_ID_PATTERN.fullmatch(self.model_id) is None
        ):
            raise ManagedModelManifestError(
                "model_id must match [a-z0-9]+(?:[._-][a-z0-9]+)*."
            )
        for field in ("display_name", "license_name", "attribution_text", "summary"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ManagedModelManifestError(f"{field} must be a non-empty string.")
        if not isinstance(self.source_url, str) or not self.source_url.startswith(
            "https://"
        ):
            raise ManagedModelManifestError("source_url must be an https:// URL.")
        for field in ("size_bytes", "minimum_free_ram_bytes"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ManagedModelManifestError(f"{field} must be a positive integer.")
        if (
            not isinstance(self.sha256, str)
            or _SHA256_PATTERN.fullmatch(self.sha256) is None
        ):
            raise ManagedModelManifestError(
                "sha256 must be 64 lowercase hexadecimal characters."
            )
        # A manifest-controlled filename becomes a path segment on disk, so it
        # must never be able to escape the install directory.
        if (
            not isinstance(self.filename, str)
            or _FILENAME_PATTERN.fullmatch(self.filename) is None
        ):
            raise ManagedModelManifestError(
                "filename must be a plain '*.gguf' name with no path separators."
            )


@dataclass(frozen=True, slots=True)
class ManagedModelCatalog:
    """The full set of pinned models, with one designated default."""

    artifacts: tuple[ManagedModelArtifact, ...]
    default_model_id: str

    def __post_init__(self) -> None:
        """Validate uniqueness and that the declared default actually exists."""
        if not self.artifacts:
            raise ManagedModelManifestError("artifacts must not be empty.")
        seen: set[str] = set()
        for artifact in self.artifacts:
            if not isinstance(artifact, ManagedModelArtifact):
                raise ManagedModelManifestError(
                    "artifacts must contain only ManagedModelArtifact instances."
                )
            if artifact.model_id in seen:
                raise ManagedModelManifestError(
                    f"Duplicate model_id '{artifact.model_id}' in catalog."
                )
            seen.add(artifact.model_id)
        if self.default_model_id not in seen:
            raise ManagedModelManifestError(
                f"default_model_id '{self.default_model_id}' is not in the catalog."
            )

    def get(self, model_id: str) -> ManagedModelArtifact:
        """Return the pinned artifact for ``model_id``.

        Raises rather than falling back to the default: silently substituting a
        different model would leave the user running something they did not ask
        for, with no signal that their choice was ignored.
        """
        for artifact in self.artifacts:
            if artifact.model_id == model_id:
                return artifact
        available = ", ".join(item.model_id for item in self.artifacts)
        raise ManagedModelManifestError(
            f"Unknown model id '{model_id}'. Available: {available}."
        )

    @property
    def default(self) -> ManagedModelArtifact:
        """Return the artifact provisioned when the user expresses no choice."""
        return self.get(self.default_model_id)


QWEN2_5_1_5B_INSTRUCT_Q4_K_M = ManagedModelArtifact(
    model_id="qwen2.5-1.5b-instruct-q4-k-m",
    display_name="Qwen2.5 1.5B Instruct (Q4_K_M)",
    source_url=(
        "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/"
        "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    ),
    size_bytes=1117320736,
    sha256="6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
    filename="qwen2.5-1.5b-instruct-q4_k_m.gguf",
    license_name="Apache-2.0",
    attribution_text="Qwen2.5 1.5B Instruct GGUF, published by Alibaba Cloud (Qwen).",
    summary=(
        "Smallest download, runs on modest hardware. Answers are short and "
        "sometimes repetitive, and it may cite a weaker paper than the best "
        "match. Misattributed claims are withheld rather than shown, so it "
        "fails by saying less."
    ),
    minimum_free_ram_bytes=4 * 1024**3,
)

QWEN2_5_7B_INSTRUCT_Q4_K_M = ManagedModelArtifact(
    model_id="qwen2.5-7b-instruct-q4-k-m",
    display_name="Qwen2.5 7B Instruct (Q4_K_M)",
    source_url=(
        "https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/"
        "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    ),
    size_bytes=4683074240,
    sha256="65b8fcd92af6b4fefa935c625d1ac27ea29dcb6ee14589c55a8f115ceaaa1423",
    filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
    license_name="Apache-2.0",
    attribution_text=(
        "Qwen2.5 7B Instruct GGUF quantizations by bartowski, from Alibaba "
        "Cloud (Qwen) base weights."
    ),
    summary=(
        "Substantially better answers: cites the right paper and states "
        "specific findings. Larger download and wants roughly 8 GB of free "
        "RAM."
    ),
    minimum_free_ram_bytes=8 * 1024**3,
)

MANAGED_MODEL_CATALOG = ManagedModelCatalog(
    artifacts=(QWEN2_5_1_5B_INSTRUCT_Q4_K_M, QWEN2_5_7B_INSTRUCT_Q4_K_M),
    # The small model is the default deliberately: the product guarantees no
    # GPU requirement and modest hardware, and a 4.4 GB first-run download
    # would contradict that for a user who only wants to try the tool.
    default_model_id=QWEN2_5_1_5B_INSTRUCT_Q4_K_M.model_id,
)
