"""Local, non-sensitive product and support information."""
from __future__ import annotations

import json
import platform
import shutil
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.services.diagnostics import optional_component_diagnostics
from app.services.paths import resource_root, user_paths
from app.services.settings import Settings

PACKAGE_NAME = "lore-oculta-studio"
SUPPORT_EMAIL = "gabriel.ramalhos97@gmail.com"
DOCUMENTS = ("EULA.md", "PRIVACY.md", "DISTRIBUTION.md", "THIRD_PARTY_NOTICES.txt", "COMPONENT_MANIFEST.json")


def app_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        # Development runs have not necessarily installed package metadata.
        import tomllib
        return tomllib.loads((resource_root() / "pyproject.toml").read_text(encoding="utf8"))["project"]["version"]


def product_information() -> dict:
    root = resource_root()
    return {
        "name": "Lore Oculta Studio",
        "version": app_version(),
        "support": SUPPORT_EMAIL,
        "data_path": str(user_paths().root),
        "documents": {name: str(root / name) if (root / name).is_file() else None for name in DOCUMENTS},
    }


def sanitized_diagnostics(settings: Settings) -> dict:
    """Report only environment state; never serialize settings, projects or credentials."""
    info = product_information()
    return {
        "product": {key: info[key] for key in ("name", "version", "support")},
        "platform": {"system": platform.system(), "release": platform.release(), "python": platform.python_version()},
        "user_data_path": info["data_path"],
        "documents_available": {name: path is not None for name, path in info["documents"].items()},
        "optional_components": optional_component_diagnostics(settings),
        "tools_available": {"ffmpeg": bool(settings.ffmpeg and (Path(settings.ffmpeg).is_file() or shutil.which(settings.ffmpeg))),
                            "kokoro_python": bool(settings.kokoro_python and Path(settings.kokoro_python).is_file())},
    }


def export_sanitized_diagnostics(target: Path, settings: Settings) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(sanitized_diagnostics(settings), ensure_ascii=False, indent=2), encoding="utf8")
    return target
