"""Unit tests for cross-platform storage path resolution."""

from pathlib import Path, PureWindowsPath

from econ_paper_cli.adapters.storage_paths import (
    get_default_db_path,
    get_default_storage_dir,
)


def test_storage_dir_windows_localappdata() -> None:
    env = {"LOCALAPPDATA": "C:\\Users\\Test\\AppData\\Local"}
    path = get_default_storage_dir(env=env, system="Windows")
    assert path == Path("C:\\Users\\Test\\AppData\\Local") / "econpapers"
    assert PureWindowsPath(path) == PureWindowsPath(
        "C:\\Users\\Test\\AppData\\Local\\econpapers"
    )


def test_storage_dir_windows_appdata_fallback() -> None:
    env = {"APPDATA": "C:\\Users\\Test\\AppData\\Roaming"}
    path = get_default_storage_dir(env=env, system="Windows")
    assert path == Path("C:\\Users\\Test\\AppData\\Roaming") / "econpapers"
    assert PureWindowsPath(path) == PureWindowsPath(
        "C:\\Users\\Test\\AppData\\Roaming\\econpapers"
    )


def test_storage_dir_macos() -> None:
    env: dict[str, str] = {}
    path = get_default_storage_dir(env=env, system="Darwin")
    expected = Path.home() / "Library" / "Application Support" / "econpapers"
    assert path == expected


def test_storage_dir_linux_xdg() -> None:
    env = {"XDG_DATA_HOME": "/custom/share"}
    path = get_default_storage_dir(env=env, system="Linux")
    assert path == Path("/custom/share/econpapers")


def test_storage_dir_linux_default_fallback() -> None:
    env: dict[str, str] = {}
    path = get_default_storage_dir(env=env, system="Linux")
    expected = Path.home() / ".local" / "share" / "econpapers"
    assert path == expected


def test_storage_dir_library_dir_override() -> None:
    env = {"ECONPAPERS_LIBRARY_DIR": "/override/library/path"}
    path = get_default_storage_dir(env=env, system="Linux")
    assert path == Path("/override/library/path")


def test_db_path_resolution_with_library_dir() -> None:
    env = {"ECONPAPERS_LIBRARY_DIR": "/override/library/path"}
    db_path = get_default_db_path(env=env, system="Linux")
    assert db_path == Path("/override/library/path/econpapers.db")


def test_explicit_path_precedence_over_library_dir(monkeypatch) -> None:
    monkeypatch.setenv("ECONPAPERS_LIBRARY_DIR", "/env/library/path")
    from econ_paper_cli.adapters.sqlite_storage import SQLiteStorage

    explicit_path = Path("/explicit/custom.db")
    storage = SQLiteStorage(db_path=explicit_path)
    assert storage.db_path == explicit_path
