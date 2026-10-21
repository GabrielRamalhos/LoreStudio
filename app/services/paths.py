"""Locations for immutable application files and mutable user data."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Lore Oculta Studio"


def application_root() -> Path:
    """Return the installed resource directory (also works from PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", application_root()))



def user_data_root() -> Path:
    """Keep all writes outside Program Files; an override supports portable QA."""
    override = os.environ.get("LORE_OCULTA_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Gabriel Ramalhos" / APP_NAME
    return Path.home() / ".local" / "share" / "gabriel-ramalhos" / "lore-oculta-studio"


@dataclass(frozen=True)
class UserPaths:
    root: Path
    settings: Path
    database: Path
    projects: Path
    logs: Path


def user_paths() -> UserPaths:
    root = user_data_root()
    return UserPaths(root, root / "config" / "settings.json", root / "lore_oculta.db", root / "projects", root / "logs")


def _copy_if_missing(source: Path, target: Path) -> None:
    if source.is_file() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def migrate_legacy_data(paths: UserPaths | None = None, legacy_root: Path | None = None) -> UserPaths:
    """Copy (never overwrite/delete) data made by pre-installer versions."""
    paths = paths or user_paths()
    legacy_root = (legacy_root or Path.cwd()).resolve()
    paths.root.mkdir(parents=True, exist_ok=True)
    _copy_if_missing(legacy_root / "config" / "settings.json", paths.settings)
    _copy_if_missing(legacy_root / "lore_oculta.db", paths.database)
    source_projects = legacy_root / "projects"
    if source_projects.is_dir():
        paths.projects.mkdir(parents=True, exist_ok=True)
        for source in source_projects.iterdir():
            target = paths.projects / source.name
            if not target.exists():
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
    paths.logs.mkdir(parents=True, exist_ok=True)
    return paths
