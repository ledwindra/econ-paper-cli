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


def test_storage_dir_env_override() -> None:
    env = {"ECONPAPERS_STORAGE_DIR": "/override/dir"}
    path = get_default_storage_dir(env=env, system="Linux")
    assert path == Path("/override/dir")


def test_db_path_resolution() -> None:
    env = {"XDG_DATA_HOME": "/custom/share"}
    db_path = get_default_db_path(env=env, system="Linux")
    assert db_path == Path("/custom/share/econpapers/econpapers.db")


def test_db_path_env_override() -> None:
    env = {"ECONPAPERS_DB_PATH": "/custom/path/my_database.db"}
    db_path = get_default_db_path(env=env, system="Linux")
    assert db_path == Path("/custom/path/my_database.db")
