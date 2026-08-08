"""M5 release-readiness tier 1: the checks that must pass in the default suite.

These are service-level tests. The public CLI entry points
(``services/commands.py``) expose no injection seams — they always construct
the real provisioners, downloader, extractor, and generator — so nothing here
is ``econpapers chat`` coverage, and it must not be described as such. The
equivalent end-to-end checks against real artifacts live in the opt-in
``integration_tests/`` tier and are run from ``docs/release-checklist.md``.

Covered here:

- **No-upload.** A socket guard around each of the four application service
  entry points, plus a negative control proving the guard actually fires.
  Scope and boundaries are stated in ``MVP-PLAN.md`` under "No-upload
  criterion": ``setup``/``update`` artifact downloads are out of scope, and so
  is whatever a user-supplied ``llama-completion`` binary does in its own
  process — a guard installed here cannot see into a subprocess.
- **Concurrency.** A real second process writing to the library while an
  interactive shell session is open, asserting the session's snapshot is
  immutable.
- **Cached-ready failure mapping.** The current, deliberately-unchanged
  behavior when a ready generator's executable disappears mid-session.
"""

import io
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# pytest's default "prepend" import mode puts this file's directory on
# sys.path, so the sibling helper imports by bare name. The concurrency
# writer subprocess below relies on the same directory being importable.
from _release_fixtures import (
    DETECTED_PLATFORM,
    MODEL_ARTIFACT,
    MODEL_CATALOG,
    managed_install,
)
from _release_fixtures import library_record as _library_record

from econ_paper_cli.adapters.config_storage import JSONConfigStorage
from econ_paper_cli.adapters.llama_cpp import (
    LlamaCppProcessError,
    LlamaCppReadinessError,
)
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage
from econ_paper_cli.domain import (
    Citation,
    EarlySectionLibraryRecord,
    ExtractedPDFPage,
    PDFDocumentMetadata,
    PDFExtractionResult,
)
from econ_paper_cli.protocols import (
    GenerationRequest,
    GenerationResponse,
    Generator,
)
from econ_paper_cli.protocols.pdf_extraction import PDFExtractor
from econ_paper_cli.services.chat_command import (
    ChatCommandOptions,
    run_chat_command,
)
from econ_paper_cli.services.interactive_shell import (
    ShellCommandOptions,
    run_interactive_shell,
)
from econ_paper_cli.services.model_provisioning import verify_managed_model
from econ_paper_cli.services.single_paper_analysis_cli import (
    AnalyzeCommandOptions,
    run_single_paper_analysis_command,
)
from econ_paper_cli.services.status_command import (
    StatusCommandOptions,
    run_status_command,
)
from econ_paper_cli.services.update_command import (
    UpdateCommandOptions,
    run_update_command,
)

# Declared by the synthetic runtime fixture's executable bytes.
_RUNTIME_VERSION_MARKER = "synthetic-marker-1.0"

_ABSTRACT = "Abstract trade policy evidence."
_INTRODUCTION = "Introduction trade policy evidence."


# --- Shared fakes ------------------------------------------------------------


class _AnsweringGenerator(Generator):
    """Answer every request by citing exactly the evidence it was given."""

    def __init__(self) -> None:
        self.call_count = 0

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.call_count += 1
        return GenerationResponse(
            answer_text="Trade policy evidence supports a descriptive answer.",
            citations=tuple(
                Citation(
                    citation_id=f"e{item.rank}",
                    paper_id=item.passage.paper_id,
                    passage_id=item.passage.passage_id,
                )
                for item in request.evidence
            ),
            generation_method="fake-generator",
            abstained=False,
            abstention_reason=None,
            finding_kinds=(),
        )


class _FakePDFExtractor(PDFExtractor):
    """Return fixed page text without opening the file."""

    def extract(self, source_path: Path) -> PDFExtractionResult:
        return PDFExtractionResult(
            source_path=source_path.resolve(),
            pages=(
                ExtractedPDFPage(
                    1,
                    "Abstract\nWe evaluate trade policy.\n\n"
                    "1. Introduction\nTrade policy affects prices on page 1.",
                ),
                ExtractedPDFPage(
                    2,
                    "1. Introduction (continued)\n"
                    "Trade policy affects prices on page 2 as well.",
                ),
            ),
            page_count=2,
            metadata=PDFDocumentMetadata(
                title="Trade Policy Paper", author_text="Ada Economist"
            ),
            extraction_method="synthetic",
            parser_version="1.0",
        )


def _populated_storage(
    db_path: Path, record: EarlySectionLibraryRecord
) -> SQLiteStorage:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    storage.save_early_section_record(record)
    return storage


def _scripted_stdin(*lines: str) -> io.StringIO:
    return io.StringIO("".join(f"{line}\n" for line in lines))


# --- No-upload: the guard ----------------------------------------------------


class NetworkAccessAttempted(AssertionError):
    """Raised when guarded code attempts any outbound network access."""


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make every outbound network primitive raise.

    Covers name resolution as well as connection: a path that only resolved a
    hostname and never connected would still be leaking the fact of a query,
    and ``getaddrinfo`` is where that would show up.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise NetworkAccessAttempted(
            "Application code attempted outbound network access. Only setup "
            "and update may reach the network, and only to download pinned "
            "artifacts."
        )

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    yield


def test_the_guard_itself_blocks_every_outbound_primitive(no_network: None) -> None:
    """Negative control. Without this, a guard that silently did nothing would
    let every test below pass while proving nothing at all."""
    with pytest.raises(NetworkAccessAttempted):
        socket.create_connection(("192.0.2.1", 443), timeout=1)
    with pytest.raises(NetworkAccessAttempted):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(NetworkAccessAttempted):
        socket.getaddrinfo("example.invalid", 443)


# --- No-upload: the four application paths -----------------------------------


def test_chat_service_answers_without_any_network_access(
    tmp_path: Path, no_network: None
) -> None:
    storage = _populated_storage(tmp_path / "chat.db", _library_record(tmp_path))
    out = io.StringIO()

    exit_code = run_chat_command(
        ChatCommandOptions(
            question="trade policy",
            executable_path=tmp_path / "llama-cli",
            model_path=tmp_path / "model.gguf",
            model_id="test-model",
            model_bytes=11,
            model_checksum="b" * 64,
            db_path=tmp_path / "chat.db",
            top_k=2,
        ),
        storage=storage,
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        generator_provider=lambda _options: _AnsweringGenerator(),
        stdout=out,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert "Trade policy evidence supports a descriptive answer." in out.getvalue()


def test_shell_service_answers_without_any_network_access(
    tmp_path: Path, no_network: None
) -> None:
    storage = _populated_storage(tmp_path / "shell.db", _library_record(tmp_path))
    out = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(db_path=tmp_path / "shell.db"),
        storage=storage,
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        generator_provider=_AnsweringGenerator,
        stdin=_scripted_stdin("trade policy", "/exit"),
        stdout=out,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert "Trade policy evidence supports a descriptive answer." in out.getvalue()


def test_analyze_service_stores_a_paper_without_any_network_access(
    tmp_path: Path, no_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf_path = (tmp_path / "paper.pdf").resolve()
    pdf_path.write_bytes(b"%PDF-1.4 synthetic content")
    (tmp_path / "llama-cli").write_bytes(b"dummy")
    (tmp_path / "model.gguf").write_bytes(b"dummy_model")
    db_path = tmp_path / "analyze.db"
    storage = SQLiteStorage(db_path)
    storage.initialize()

    exit_code = run_single_paper_analysis_command(
        AnalyzeCommandOptions(
            target_path=pdf_path,
            executable_path=tmp_path / "llama-cli",
            model_path=tmp_path / "model.gguf",
            model_id="test-model",
            model_bytes=11,
            model_checksum="b" * 64,
            db_path=db_path,
        ),
        extractor=_FakePDFExtractor(),
        generator=_AnsweringGenerator(),
        storage=storage,
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
    )

    capsys.readouterr()
    assert exit_code == 0
    assert storage.list_single_paper_analyses(), "analysis must have been stored"


def test_status_service_reports_a_configured_install_without_network_access(
    tmp_path: Path, no_network: None
) -> None:
    """Drives the *configured* path, not the absent-config short-circuit.

    With no durable config, ``status`` reports "not configured" and never
    reaches runtime or model readiness — so guarding that path proves almost
    nothing. This provisions a synthetic managed runtime and model, saves the
    config, and injects both readiness checkers, so the report is produced
    with every branch that inspects an artifact actually exercised.
    """
    install = managed_install(tmp_path)
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(install.config)

    db_path = tmp_path / "library.db"
    storage = _populated_storage(db_path, _library_record(tmp_path))

    runtime_checks: list[tuple[Path, str]] = []
    model_checks: list[object] = []

    def runtime_checker(executable_path: Path, version_marker: str) -> None:
        runtime_checks.append((executable_path, version_marker))

    def model_checker(config: object) -> None:
        model_checks.append(config)

    out = io.StringIO()
    exit_code = run_status_command(
        StatusCommandOptions(db_path=db_path),
        config_backend=config_backend,
        storage=storage,
        runtime_readiness_checker=runtime_checker,
        model_readiness_checker=model_checker,
        stdout=out,
    )

    rendered = out.getvalue()
    assert exit_code == 0
    # The configured branches really were taken.
    assert runtime_checks, "runtime readiness was never checked"
    assert model_checks, "model readiness was never checked"
    # ...and the report reflects verified artifacts rather than a
    # "not configured" short-circuit.
    assert "Configuration: present and valid" in rendered
    assert f"Model ID: {install.config.model_id}" in rendered
    assert "Runtime State: verified" in rendered
    assert "Model State: verified" in rendered
    assert "Overall Ready: True" in rendered
    assert "Paper Count: 1" in rendered


def test_status_service_reports_an_unconfigured_install_without_network_access(
    tmp_path: Path, no_network: None
) -> None:
    """The absent-config path is still worth guarding, but as its own case
    rather than as a stand-in for the configured one above."""
    out = io.StringIO()

    exit_code = run_status_command(
        StatusCommandOptions(),
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        storage=SQLiteStorage(str(tmp_path / "missing.db"), read_only=True),
        stdout=out,
    )

    assert exit_code == 0
    assert out.getvalue().strip()


# --- Concurrency: an immutable session snapshot ------------------------------


_WRITER_DRIVER = """\
import sys
from pathlib import Path

fixtures_dir, db_path, title, checksum, filename = sys.argv[1:6]
sys.path.insert(0, fixtures_dir)

from _release_fixtures import library_record
from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage

storage = SQLiteStorage(Path(db_path))
storage.initialize()
storage.save_early_section_record(
    library_record(
        Path(db_path).parent,
        title=title,
        checksum=checksum,
        source_filename=filename,
    )
)
storage.close()
"""


class _StdinWritingMidSession(io.StringIO):
    """Scripted stdin that runs a real writer subprocess partway through.

    Driving the writer from ``readline`` is what makes this a genuine
    concurrency test: the second process runs while the shell session is open
    and holding its connection, not before or after it.
    """

    def __init__(self, lines: tuple[str, ...], *, before_line: int, writer) -> None:
        super().__init__("".join(f"{line}\n" for line in lines))
        self._before_line = before_line
        self._writer = writer
        self._reads = 0
        self.writer_returncode: int | None = None

    def readline(self, size: int = -1) -> str:  # type: ignore[override]
        if self._reads == self._before_line:
            self.writer_returncode = self._writer()
        self._reads += 1
        return super().readline(size)


def _turn_segments(output: str) -> list[str]:
    """Split rendered shell output on the prompt into per-interaction blocks.

    Segment 0 is the banner; each later segment is exactly one turn's
    rendered output.
    """
    return output.split("econpapers> ")


def test_a_concurrent_writer_cannot_change_an_open_shell_session(
    tmp_path: Path,
) -> None:
    """Tier 1 of the concurrency scenario.

    What this establishes precisely: cross-process compatibility with an
    *idle* read-only shell. The shell holds an open connection but no active
    read transaction between turns, so the writer meets no lock to contend
    for — this is not a lock-contention test, and must not be cited as one.
    Lock contention is deliberately out of scope because the shell never
    holds a long-lived read transaction.

    The writer is a real subprocess using the same ``SQLiteStorage`` adapter
    ``analyze`` uses, but it skips PDF extraction and generation: the real
    ``econpapers analyze`` CLI builds a real ``LlamaCppGenerator`` with no
    injection seam, so it belongs to the opt-in tier, not here.
    """
    db_path = tmp_path / "library.db"
    storage = _populated_storage(db_path, _library_record(tmp_path))

    def run_writer() -> int:
        driver = tmp_path / "writer.py"
        driver.write_text(_WRITER_DRIVER, encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(driver),
                str(Path(__file__).parent),
                str(db_path),
                "Concurrently Added Paper",
                "d" * 64,
                "added-during-session.pdf",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        return completed.returncode

    # The writer runs immediately before the third line is read, i.e. with the
    # session open and one question and one /show already rendered.
    stdin = _StdinWritingMidSession(
        ("trade policy", "/show e1", "trade policy", "/show e1", "/exit"),
        before_line=2,
        writer=run_writer,
    )
    out = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(db_path=db_path),
        storage=storage,
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        generator_provider=_AnsweringGenerator,
        stdin=stdin,
        stdout=out,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert stdin.writer_returncode == 0, "the writer subprocess must have run"
    rendered = out.getvalue()
    segments = _turn_segments(rendered)
    assert len(segments) == 6, rendered

    # Existing turns are byte-identical across the write: same question, and
    # the same /show evidence, rendered before and after the writer ran.
    assert segments[1] == segments[3], "answer turn changed across a concurrent write"
    assert segments[2] == segments[4], "/show output changed across a concurrent write"

    # No mixed snapshot: the banner's counts are the ones captured at open,
    # and the concurrently added paper is invisible for the whole session.
    assert "Paper Count: 1" in segments[0]
    assert "Concurrently Added Paper" not in rendered

    # The writer actually wrote — otherwise none of the above proves anything.
    verifier = SQLiteStorage(db_path)
    titles = {record.paper.title for record in verifier.list_early_section_records()}
    verifier.close()
    assert "Concurrently Added Paper" in titles

    # And a *new* session sees it, confirming the snapshot was per-session
    # rather than the paper never having been visible at all.
    restarted_out = io.StringIO()
    restarted_storage = SQLiteStorage(db_path)
    restarted_storage.initialize()
    run_interactive_shell(
        ShellCommandOptions(db_path=db_path),
        storage=restarted_storage,
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        generator_provider=_AnsweringGenerator,
        stdin=_scripted_stdin("/exit"),
        stdout=restarted_out,
        stderr=io.StringIO(),
    )
    assert "Paper Count: 2" in restarted_out.getvalue()


# --- Cached-ready failure mapping (current behavior, deliberately unchanged) --


class _ExecutableBackedGenerator(Generator):
    """A generator that models the adapter's readiness caching.

    ``LlamaCppGenerator`` verifies readiness lazily inside ``generate()`` and
    caches the result (``adapters/llama_cpp.py``), so once one turn succeeds a
    later turn goes straight to launching the process. This fake reproduces
    that state machine against a real file on disk: the first call performs
    the readiness check, later calls skip it and fail the way
    ``SubprocessRunner._start_process`` does when the executable has gone —
    with ``LlamaCppProcessError``, not a readiness error.

    ``readiness_check`` is what the first call runs. The default only checks
    that the file exists, which is enough for the executable-loss scenario
    where existence is precisely what changes. Scenarios that need to prove a
    repaired runtime is genuinely *usable* — not merely present — inject a
    stricter check; see ``_strict_readiness_check`` below.
    """

    def __init__(
        self,
        executable_path: Path,
        *,
        readiness_check: Callable[[], None] | None = None,
    ) -> None:
        self._executable_path = executable_path
        self._readiness_check = readiness_check
        self._ready = False

    def _default_readiness_check(self) -> None:
        if not self._executable_path.is_file():
            raise LlamaCppReadinessError("Configured llama.cpp executable is missing.")

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        if not self._ready:
            if self._readiness_check is not None:
                self._readiness_check()
            else:
                self._default_readiness_check()
            self._ready = True
        elif not self._executable_path.is_file():
            raise LlamaCppProcessError(
                "Unable to start the configured local generation runtime."
            )
        return _AnsweringGenerator().generate(request)


def _strict_readiness_check(executable_path: Path, version_marker: str) -> None:
    """Validate a runtime the way readiness actually would, minus the exec.

    The real check runs the binary and looks for its version marker in the
    output (``adapters/llama_cpp.py``). A test cannot portably execute a
    synthetic binary — the fixture's shell stub is meaningless on Windows —
    so this inspects the installed bytes for the same marker instead. That
    still fails on a truncated, corrupted, or wrong-version install, which is
    the point: ``Path.is_file()`` alone would pass all three.
    """
    if not executable_path.is_file():
        raise LlamaCppReadinessError(
            f"Configured llama.cpp executable is missing: {executable_path}."
        )
    payload = executable_path.read_bytes()
    if version_marker.encode("utf-8") not in payload:
        raise LlamaCppReadinessError(
            f"Installed runtime does not declare version marker '{version_marker}'."
        )


class _StdinRemovingExecutable(io.StringIO):
    """Scripted stdin that deletes the runtime executable partway through."""

    def __init__(self, lines: tuple[str, ...], *, before_line: int, target: Path):
        super().__init__("".join(f"{line}\n" for line in lines))
        self._before_line = before_line
        self._target = target
        self._reads = 0

    def readline(self, size: int = -1) -> str:  # type: ignore[override]
        if self._reads == self._before_line:
            self._target.unlink()
        self._reads += 1
        return super().readline(size)


def test_a_ready_generator_losing_its_executable_reports_internal_failure(
    tmp_path: Path,
) -> None:
    """Pins today's classification rather than asserting the one we might
    prefer.

    A turn that fails because the executable vanished *after* the generator
    became ready surfaces as ``INTERNAL_FAILURE``, because
    ``LlamaCppProcessError`` falls in the shell's internal-failure group
    (``services/interactive_shell.py``). Only the *unconstructed* case is a
    typed failure, since that one fails at ``check_readiness()``.

    M5 does not change this mapping — reclassifying a user-visible outcome is
    a behavior change with its own exit-code and documentation consequences,
    and belongs to its own issue. This test exists so that change, if taken,
    updates an assertion deliberately instead of discovering the mapping by
    accident. It is listed as a known limitation in
    ``docs/release-checklist.md``.

    The ordering matters and is the point: readiness is lazy, so removing the
    executable *before* any successful turn would produce a readiness error
    and a typed failure — the wrong path, proving nothing about this one.
    """
    db_path = tmp_path / "library.db"
    storage = _populated_storage(db_path, _library_record(tmp_path))
    executable = tmp_path / "llama-completion"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")

    # Turn 1 succeeds and marks the generator ready; a /show captures the
    # evidence rendering; the executable is then removed; turn 2 goes straight
    # to launching it and fails; a second /show then shows the evidence has
    # been cleared, because /show is scoped to the latest turn. See the
    # assertions below for why that is correct rather than a regression.
    stdin = _StdinRemovingExecutable(
        ("trade policy", "/show e1", "trade policy", "/show e1", "/exit"),
        before_line=2,
        target=executable,
    )
    out = io.StringIO()
    err = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(db_path=db_path),
        storage=storage,
        config_backend=JSONConfigStorage(tmp_path / "config.json"),
        generator_provider=lambda: _ExecutableBackedGenerator(executable),
        stdin=stdin,
        stdout=out,
        stderr=err,
    )

    rendered_out = out.getvalue()
    rendered_err = err.getvalue()

    # Turn 1 answered normally, on stdout.
    assert "Outcome: answered" in rendered_out
    assert "Trade policy evidence supports a descriptive answer." in rendered_out

    # Turn 2 is an internal failure, rendered to stderr (where the shell sends
    # both failure classes), carrying an actionable message. Not a typed
    # failure, not a wrong answer, and not an unhandled exception escaping the
    # loop — the session stays usable and exits cleanly afterwards.
    assert "Outcome: internal_failure" in rendered_err
    assert "Outcome: typed_failure" not in rendered_err
    assert "Unable to start the configured local generation runtime." in rendered_err
    assert "Answer:" not in rendered_err

    # The failing turn did not corrupt the earlier one: exactly one answered
    # turn was rendered, and its text is intact.
    assert rendered_out.count("Outcome: answered") == 1
    assert (
        rendered_out.count("Trade policy evidence supports a descriptive answer.") == 1
    )

    # /show before and after the failure. These are deliberately *not*
    # expected to match, and the difference is by design rather than damage:
    # ``last_turn_citations`` is scoped to the most recent turn and cleared by
    # any non-answered turn, so evidence is never left visible as though it
    # belonged to the question just asked
    # (``services/interactive_shell.py``, ``last_turn_citations``).
    #
    # This is the opposite of the concurrency scenario, where /show *must*
    # stay byte-identical: there the session is untouched and only an outside
    # writer changed, whereas here the session itself moved on to a turn that
    # produced no evidence.
    segments = _turn_segments(rendered_out)
    assert len(segments) == 6, rendered_out
    assert "Passage ID:" in segments[2], "the first /show must render evidence"
    assert "No evidence is available yet" in segments[4]

    # What must be preserved is the earlier turn's own rendered output, and it
    # is: turn 1 is still intact above.
    assert "Outcome: answered" in segments[1]

    # A failed turn does not change the shell's exit code when the user then
    # exits cleanly; pinned here because it is easy to change by accident.
    assert exit_code == 0


# --- Concurrency: update while a shell session is open -----------------------


class _StdinRunningUpdate(io.StringIO):
    """Scripted stdin that runs ``update`` partway through the session."""

    def __init__(self, lines: tuple[str, ...], *, before_line: int, updater):
        super().__init__("".join(f"{line}\n" for line in lines))
        self._before_line = before_line
        self._updater = updater
        self._reads = 0
        self.update_exit_code: int | None = None
        self.update_output: str = ""

    def readline(self, size: int = -1) -> str:  # type: ignore[override]
        if self._reads == self._before_line:
            self.update_exit_code, self.update_output = self._updater()
        self._reads += 1
        return super().readline(size)


def test_a_successful_update_repair_leaves_the_next_shell_turn_working(
    tmp_path: Path,
) -> None:
    """The second writer in the concurrency scenario: ``update``, not
    ``analyze``.

    A repair is reported as ``REPAIRED`` only after the runtime has been
    downloaded, verified, readiness-checked and promoted, and ``update`` does
    not rewrite durable config — so the executable path the shell resolves
    still points into a verified install. A turn that constructs its generator
    for the first time *after* a successful update must therefore succeed.
    Accepting a typed failure here would be accepting a regression with no
    mechanism behind it, which is why this asserts success rather than
    tolerating either outcome.

    The generator is backed by the real executable path so the assertion is
    tied to update's actual filesystem effect, not to a fake that would have
    answered regardless.
    """
    db_path = tmp_path / "library.db"
    storage = _populated_storage(db_path, _library_record(tmp_path))
    install = managed_install(tmp_path)
    config_backend = JSONConfigStorage(tmp_path / "config.json")
    config_backend.save(install.config)

    # Corrupt the installed runtime so `update` has a real repair to perform.
    install.executable_path.write_bytes(b"corrupted executable bytes")
    install.downloader.download_count = 0

    # Negative control for the readiness check itself. Without this, a check
    # that silently accepted anything would make every assertion below pass
    # while proving nothing — the same failure mode the socket guard's control
    # exists to rule out.
    with pytest.raises(LlamaCppReadinessError):
        _strict_readiness_check(install.executable_path, _RUNTIME_VERSION_MARKER)

    def readiness_check() -> None:
        """What the shell's first turn must get past after the repair.

        Validates the runtime's installed bytes and declared version, and
        verifies the model against its pinned size and checksum — so a
        present-but-unusable runtime or a corrupted model fails here rather
        than being waved through by an existence test.
        """
        _strict_readiness_check(install.executable_path, _RUNTIME_VERSION_MARKER)
        if not verify_managed_model(install.config.model_path, MODEL_ARTIFACT):
            raise LlamaCppReadinessError(
                "Configured model failed size/checksum verification."
            )

    def run_update() -> tuple[int, str]:
        update_out = io.StringIO()
        code = run_update_command(
            UpdateCommandOptions(),
            config_backend=config_backend,
            runtime_dir=install.runtime_dir,
            model_dir=install.model_dir,
            downloader=install.downloader,
            extractor=install.extractor,
            runtime_manifest=install.runtime_manifest,
            model_catalog=MODEL_CATALOG,
            runtime_readiness_checker=_strict_readiness_check,
            detected_platform=DETECTED_PLATFORM,
            stdout=update_out,
            stderr=io.StringIO(),
        )
        return code, update_out.getvalue()

    # No question is asked before the update, so the session's generator is
    # still unconstructed when the repair happens — the case the plan singles
    # out.
    stdin = _StdinRunningUpdate(
        ("/status", "trade policy", "/exit"),
        before_line=1,
        updater=run_update,
    )
    out = io.StringIO()
    err = io.StringIO()

    exit_code = run_interactive_shell(
        ShellCommandOptions(db_path=db_path),
        storage=storage,
        config_backend=config_backend,
        generator_provider=lambda: _ExecutableBackedGenerator(
            install.executable_path, readiness_check=readiness_check
        ),
        stdin=stdin,
        stdout=out,
        stderr=err,
    )

    # update itself succeeded, and actually repaired rather than reusing.
    assert stdin.update_exit_code == 0, stdin.update_output
    assert "repaired" in stdin.update_output.lower(), stdin.update_output
    assert install.downloader.download_count > 0, "the repair must have re-downloaded"

    # The repaired executable is not merely present at the configured path —
    # it passes the same content-and-version validation the turn had to.
    _strict_readiness_check(install.executable_path, _RUNTIME_VERSION_MARKER)
    assert verify_managed_model(install.config.model_path, MODEL_ARTIFACT)

    # The first generator construction after the repair succeeds.
    assert exit_code == 0
    rendered_out = out.getvalue()
    assert "Outcome: answered" in rendered_out
    assert "Outcome: typed_failure" not in err.getvalue()
    assert "Outcome: internal_failure" not in err.getvalue()

    # The session's snapshot is still the one captured at open.
    assert "Paper Count: 1" in rendered_out
