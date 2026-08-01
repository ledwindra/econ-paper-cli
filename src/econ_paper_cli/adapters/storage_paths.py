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
    2. ECONPAPERS_STORAGE_DIR environment variable (if non-empty)
    3. Windows: %LOCALAPPDATA% / app_name -> %APPDATA% / app_name -> ~/AppData/Local/app_name
    4. macOS: ~/Library/Application Support / app_name
    5. Linux/POSIX: ${XDG_DATA_HOME:-~/.local/share} / app_name
    """
    env_map = os.environ if env is None else env
    custom_library_dir = env_map.get("ECONPAPERS_LIBRARY_DIR", "").strip()
    if custom_library_dir:
        return Path(custom_library_dir)

    custom_storage_dir = env_map.get("ECONPAPERS_STORAGE_DIR", "").strip()
    if custom_storage_dir:
        return Path(custom_storage_dir)

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
    1. ECONPAPERS_DB_PATH environment variable (if non-empty)
    2. get_default_storage_dir(...) / db_filename
    """
    env_map = os.environ if env is None else env
    custom_db = env_map.get("ECONPAPERS_DB_PATH", "").strip()
    if custom_db:
        return Path(custom_db)

    return (
        get_default_storage_dir(app_name=app_name, env=env, system=system) / db_filename
    )
