"""Service tests for platform/architecture detection."""

import pytest

from econ_paper_cli.domain import SupportedArchitecture, SupportedPlatform
from econ_paper_cli.services.platform_detection import detect_current_platform


@pytest.mark.parametrize(
    "system,machine,expected_platform,expected_architecture",
    [
        ("Darwin", "arm64", SupportedPlatform.MACOS, SupportedArchitecture.ARM64),
        ("Darwin", "x86_64", SupportedPlatform.MACOS, SupportedArchitecture.X86_64),
        ("Linux", "x86_64", SupportedPlatform.LINUX, SupportedArchitecture.X86_64),
        ("Linux", "aarch64", SupportedPlatform.LINUX, SupportedArchitecture.ARM64),
        ("Windows", "AMD64", SupportedPlatform.WINDOWS, SupportedArchitecture.X86_64),
        ("Windows", "amd64", SupportedPlatform.WINDOWS, SupportedArchitecture.X86_64),
    ],
)
def test_supported_combinations_detected(
    system: str,
    machine: str,
    expected_platform: SupportedPlatform,
    expected_architecture: SupportedArchitecture,
) -> None:
    detected = detect_current_platform(system=system, machine=machine)
    assert detected.platform is expected_platform
    assert detected.architecture is expected_architecture
    assert detected.is_supported is True


@pytest.mark.parametrize(
    "system,machine",
    [
        ("FreeBSD", "x86_64"),
        ("Linux", "riscv64"),
        ("Darwin", "i386"),
        ("SunOS", "sparc"),
    ],
)
def test_unsupported_combinations_report_cleanly_without_raising(
    system: str, machine: str
) -> None:
    detected = detect_current_platform(system=system, machine=machine)
    assert detected.is_supported is False
    assert detected.raw_system == system
    assert detected.raw_machine == machine


def test_defaults_to_real_platform_module_values() -> None:
    import platform as platform_module

    detected = detect_current_platform()
    assert detected.raw_system == platform_module.system()
    assert detected.raw_machine == platform_module.machine()
