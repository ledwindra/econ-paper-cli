"""Cross-platform storage path resolution for Windows, macOS, and Linux."""

import os
import platform
from collections.abc import Mapping
from pathlib import Path

DEFAULT_APP_NAME = "econpapers"


def get_default_storage_dir(
    app_name: str = DEFAULT_APP_NAME,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
) -> Path:
    """Resolve the canonical cross-platform application storage directory.

    Resolution order:
    1. ECONPAPERS_LIBRARY_DIR environment variable (if non-empty)
    2. Windows: %LOCALAPPDATA% / app_name -> %APPDATA% / app_name -> ~/AppData/Local/app_name
    3. macOS: ~/Library/Application Support / app_name
    4. Linux/POSIX: ${XDG_DATA_HOME:-~/.local/share} / app_name
    """
    env_map = os.environ if env is None else env
    custom_library_dir = env_map.get("ECONPAPERS_LIBRARY_DIR", "").strip()
    if custom_library_dir:
        return Path(custom_library_dir)

    sys_name = platform.system() if system is None else system

    if sys_name.lower() in ("windows", "win32"):
        local_app_data = env_map.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / app_name
        app_data = env_map.get("APPDATA", "").strip()
        if app_data:
            return Path(app_data) / app_name
        return Path.home() / "AppData" / "Local" / app_name

    if sys_name.lower() in ("darwin", "mac", "macos"):
        return Path.home() / "Library" / "Application Support" / app_name

    # Linux / Unix / POSIX default
    xdg_data = env_map.get("XDG_DATA_HOME", "").strip()
    if xdg_data:
        return Path(xdg_data) / app_name

    return Path.home() / ".local" / "share" / app_name


def get_default_db_path(
    db_filename: str = "econpapers.db",
    app_name: str = DEFAULT_APP_NAME,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
) -> Path:
    """Resolve the canonical cross-platform database file path.

    Resolution order:
    1. get_default_storage_dir(...) / db_filename (respecting ECONPAPERS_LIBRARY_DIR if set)
    """
    return (
        get_default_storage_dir(app_name=app_name, env=env, system=system) / db_filename
    )


def get_default_config_dir(
    app_name: str = DEFAULT_APP_NAME,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
) -> Path:
    """Resolve the canonical cross-platform local-configuration directory.

    Independent of the current working directory and of
    ``get_default_storage_dir`` (the SQLite data directory): both live under
    the same per-user application area on a given platform, but under
    distinct subdirectories, so a config-only or data-only override never
    silently affects the other.

    Resolution order:
    1. ECONPAPERS_CONFIG_DIR environment variable (if non-empty)
    2. Windows: %LOCALAPPDATA% / app_name / "config" -> %APPDATA% / app_name / "config"
       -> ~/AppData/Local/app_name/config
    3. macOS: ~/Library/Application Support / app_name / "config"
    4. Linux/POSIX: ${XDG_CONFIG_HOME:-~/.config} / app_name
    """
    env_map = os.environ if env is None else env
    custom_config_dir = env_map.get("ECONPAPERS_CONFIG_DIR", "").strip()
    if custom_config_dir:
        return Path(custom_config_dir)

    sys_name = platform.system() if system is None else system

    if sys_name.lower() in ("windows", "win32"):
        local_app_data = env_map.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / app_name / "config"
        app_data = env_map.get("APPDATA", "").strip()
        if app_data:
            return Path(app_data) / app_name / "config"
        return Path.home() / "AppData" / "Local" / app_name / "config"

    if sys_name.lower() in ("darwin", "mac", "macos"):
        return Path.home() / "Library" / "Application Support" / app_name / "config"

    # Linux / Unix / POSIX default
    xdg_config = env_map.get("XDG_CONFIG_HOME", "").strip()
    if xdg_config:
        return Path(xdg_config) / app_name

    return Path.home() / ".config" / app_name


def get_default_runtime_dir(
    app_name: str = DEFAULT_APP_NAME,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
) -> Path:
    """Resolve the canonical cross-platform managed-runtime install directory.

    Independent of both the config dir and the SQLite library dir, but
    nested under the same per-user data area as the library (this is
    downloaded binary/library data, not configuration).

    Resolution order:
    1. ECONPAPERS_RUNTIME_DIR environment variable (if non-empty)
    2. get_default_storage_dir(...) / "runtime"
    """
    env_map = os.environ if env is None else env
    custom_runtime_dir = env_map.get("ECONPAPERS_RUNTIME_DIR", "").strip()
    if custom_runtime_dir:
        return Path(custom_runtime_dir)

    return (
        get_default_storage_dir(app_name=app_name, env=env, system=system) / "runtime"
    )


def get_default_model_dir(
    app_name: str = DEFAULT_APP_NAME,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
) -> Path:
    """Resolve the canonical cross-platform managed-model install directory.

    Sibling of the managed-runtime directory and under the same per-user data
    area: a downloaded GGUF is bulk data, not configuration.

    Resolution order:
    1. ECONPAPERS_MODEL_DIR environment variable (if non-empty)
    2. get_default_storage_dir(...) / "models"
    """
    env_map = os.environ if env is None else env
    custom_model_dir = env_map.get("ECONPAPERS_MODEL_DIR", "").strip()
    if custom_model_dir:
        return Path(custom_model_dir)

    return get_default_storage_dir(app_name=app_name, env=env, system=system) / "models"


def get_default_config_path(
    config_filename: str = "config.json",
    app_name: str = DEFAULT_APP_NAME,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
) -> Path:
    """Resolve the canonical cross-platform local-configuration file path.

    Resolution order:
    1. get_default_config_dir(...) / config_filename (respecting ECONPAPERS_CONFIG_DIR if set)
    """
    return (
        get_default_config_dir(app_name=app_name, env=env, system=system)
        / config_filename
    )
