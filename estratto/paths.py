from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


APP_NAME = "Estratto"


def bundled_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def bundled_config_path() -> Path:
    return bundled_root() / "config.yaml"


def static_dir() -> Path:
    return bundled_root() / "web" / "static"


def default_data_dir() -> Path:
    override = os.environ.get("ESTRATTO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME.lower()
    return Path.home() / ".local" / "share" / APP_NAME.lower()


def default_config_path() -> Path:
    override = os.environ.get("ESTRATTO_CONFIG_PATH")
    if override:
        return Path(override).expanduser()
    return default_data_dir() / "config.yaml"


def ensure_runtime_config(config_path: str | Path | None = None) -> Path:
    path = Path(config_path).expanduser() if config_path else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        shutil.copyfile(bundled_config_path(), path)
    return path
