"""Unit tests for cross-platform storage path resolution."""

from pathlib import Path, PureWindowsPath

from econ_paper_cli.adapters.storage_paths import (
    get_default_config_dir,
    get_default_config_path,
    get_default_db_path,
    get_default_runtime_dir,
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


def test_config_dir_windows_localappdata() -> None:
    env = {"LOCALAPPDATA": "C:\\Users\\Test\\AppData\\Local"}
    path = get_default_config_dir(env=env, system="Windows")
    assert path == Path("C:\\Users\\Test\\AppData\\Local") / "econpapers" / "config"


def test_config_dir_windows_appdata_fallback() -> None:
    env = {"APPDATA": "C:\\Users\\Test\\AppData\\Roaming"}
    path = get_default_config_dir(env=env, system="Windows")
    assert path == Path("C:\\Users\\Test\\AppData\\Roaming") / "econpapers" / "config"


def test_config_dir_macos() -> None:
    env: dict[str, str] = {}
    path = get_default_config_dir(env=env, system="Darwin")
    expected = Path.home() / "Library" / "Application Support" / "econpapers" / "config"
    assert path == expected


def test_config_dir_linux_xdg() -> None:
    env = {"XDG_CONFIG_HOME": "/custom/config"}
    path = get_default_config_dir(env=env, system="Linux")
    assert path == Path("/custom/config/econpapers")


def test_config_dir_linux_default_fallback() -> None:
    env: dict[str, str] = {}
    path = get_default_config_dir(env=env, system="Linux")
    expected = Path.home() / ".config" / "econpapers"
    assert path == expected


def test_config_dir_env_override() -> None:
    env = {"ECONPAPERS_CONFIG_DIR": "/override/config/path"}
    path = get_default_config_dir(env=env, system="Linux")
    assert path == Path("/override/config/path")


def test_config_path_resolution_with_config_dir() -> None:
    env = {"ECONPAPERS_CONFIG_DIR": "/override/config/path"}
    config_path = get_default_config_path(env=env, system="Linux")
    assert config_path == Path("/override/config/path/config.json")


def test_config_dir_is_independent_of_storage_dir() -> None:
    env: dict[str, str] = {}
    storage_dir = get_default_storage_dir(env=env, system="Linux")
    config_dir = get_default_config_dir(env=env, system="Linux")
    assert storage_dir != config_dir

    db_path = get_default_db_path(env=env, system="Linux")
    config_path = get_default_config_path(env=env, system="Linux")
    assert db_path != config_path


def test_config_dir_env_override_does_not_affect_storage_dir() -> None:
    env = {"ECONPAPERS_CONFIG_DIR": "/only/config"}
    assert get_default_config_dir(env=env, system="Linux") == Path("/only/config")
    assert get_default_storage_dir(env=env, system="Linux") == (
        Path.home() / ".local" / "share" / "econpapers"
    )


def test_library_dir_env_override_does_not_affect_config_dir() -> None:
    env = {"ECONPAPERS_LIBRARY_DIR": "/only/library"}
    assert get_default_storage_dir(env=env, system="Linux") == Path("/only/library")
    assert get_default_config_dir(env=env, system="Linux") == (
        Path.home() / ".config" / "econpapers"
    )


def test_runtime_dir_nests_under_storage_dir_by_default() -> None:
    env: dict[str, str] = {}
    storage_dir = get_default_storage_dir(env=env, system="Linux")
    runtime_dir = get_default_runtime_dir(env=env, system="Linux")
    assert runtime_dir == storage_dir / "runtime"


def test_runtime_dir_env_override() -> None:
    env = {"ECONPAPERS_RUNTIME_DIR": "/override/runtime/path"}
    path = get_default_runtime_dir(env=env, system="Linux")
    assert path == Path("/override/runtime/path")


def test_runtime_dir_env_override_does_not_affect_storage_or_config_dir() -> None:
    env = {"ECONPAPERS_RUNTIME_DIR": "/only/runtime"}
    assert get_default_runtime_dir(env=env, system="Linux") == Path("/only/runtime")
    assert get_default_storage_dir(env=env, system="Linux") == (
        Path.home() / ".local" / "share" / "econpapers"
    )
    assert get_default_config_dir(env=env, system="Linux") == (
        Path.home() / ".config" / "econpapers"
    )


def test_config_and_db_paths_are_independent_of_current_working_directory(
    tmp_path, monkeypatch
) -> None:
    env = {
        "ECONPAPERS_CONFIG_DIR": "/fixed/config",
        "ECONPAPERS_LIBRARY_DIR": "/fixed/library",
    }
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()

    monkeypatch.chdir(first_dir)
    config_path_first = get_default_config_path(env=env, system="Linux")
    db_path_first = get_default_db_path(env=env, system="Linux")

    monkeypatch.chdir(second_dir)
    config_path_second = get_default_config_path(env=env, system="Linux")
    db_path_second = get_default_db_path(env=env, system="Linux")

    assert config_path_first == config_path_second == Path("/fixed/config/config.json")
    assert db_path_first == db_path_second == Path("/fixed/library/econpapers.db")
