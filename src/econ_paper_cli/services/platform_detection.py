"""Detects the current machine's platform/architecture for runtime provisioning.

Pure translation from ``platform.system()``/``platform.machine()`` into the
vocabulary ``domain.runtime_manifest`` uses. Never raises for an unsupported
combination — callers (setup/status) need a typed "unsupported" result to
report cleanly, not an exception.
"""

import platform
from dataclasses import dataclass

from econ_paper_cli.domain import SupportedArchitecture, SupportedPlatform

_PLATFORM_ALIASES: dict[str, SupportedPlatform] = {
    "Darwin": SupportedPlatform.MACOS,
    "Linux": SupportedPlatform.LINUX,
    "Windows": SupportedPlatform.WINDOWS,
}

_ARCHITECTURE_ALIASES: dict[str, SupportedArchitecture] = {
    "arm64": SupportedArchitecture.ARM64,
    "aarch64": SupportedArchitecture.ARM64,
    "x86_64": SupportedArchitecture.X86_64,
    "amd64": SupportedArchitecture.X86_64,
}


@dataclass(frozen=True, slots=True)
class DetectedPlatform:
    """The current machine's platform/architecture, if supported.

    ``platform``/``architecture`` are ``None`` when the raw values have no
    known mapping; ``raw_system``/``raw_machine`` are always preserved so an
    "unsupported platform" report can still name what was actually detected.
    """

    platform: SupportedPlatform | None
    architecture: SupportedArchitecture | None
    raw_system: str
    raw_machine: str

    @property
    def is_supported(self) -> bool:
        """Whether both platform and architecture were recognized."""
        return self.platform is not None and self.architecture is not None


def detect_current_platform(
    *,
    system: str | None = None,
    machine: str | None = None,
) -> DetectedPlatform:
    """Detect the current platform/architecture.

    ``system``/``machine`` are injectable overrides (defaulting to
    ``platform.system()``/``platform.machine()``) so tests can exercise every
    combination without depending on the actual host.
    """
    raw_system = system if system is not None else platform.system()
    raw_machine = machine if machine is not None else platform.machine()
    return DetectedPlatform(
        platform=_PLATFORM_ALIASES.get(raw_system),
        architecture=_ARCHITECTURE_ALIASES.get(raw_machine.lower()),
        raw_system=raw_system,
        raw_machine=raw_machine,
    )
