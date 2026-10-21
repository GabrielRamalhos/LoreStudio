"""Non-invasive diagnostics for integrations not shipped by the installer."""
from __future__ import annotations

import shutil
from pathlib import Path

from app.services.settings import Settings


def optional_component_diagnostics(settings: Settings) -> list[str]:
    checks = [
        ("Ollama", "opcional — instale e inicie localmente para gerar roteiros e planejamento."),
        ("ComfyUI", "opcional — instale localmente com checkpoint licenciado para gerar imagens."),
    ]
    piper_ready = bool(settings.piper_executable and (Path(settings.piper_executable).is_file() or shutil.which(settings.piper_executable)))
    checks.append(("Piper", "opcional — instale externamente e selecione executável e voz licenciada." if not piper_ready else "configurado externamente."))
    return [f"{name}: {message}" for name, message in checks]
