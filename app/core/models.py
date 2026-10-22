from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

class StepStatus(StrEnum):
    PENDING = "PENDENTE"; PROCESSING = "PROCESSANDO"; DONE = "CONCLUIDO"; ERROR = "ERRO"

@dataclass
class Project:
    id: int | None; slug: str; theme: str; category: str; format: str; duration: int
    script: str = ""; images: list[str] = field(default_factory=list); statuses: dict[str, str] = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    audio_duration: float | None = None
    history: list[dict] = field(default_factory=list)
    @property
    def size(self): return (1080, 1920) if self.format == "Short" else (1920, 1080)

def validate(theme: str, duration: int, fmt: str) -> None:
    if not theme.strip(): raise ValueError("Tema é obrigatório.")
    if fmt not in {"Short", "Longo"}: raise ValueError("Formato inválido.")
    # A source-video batch can be any length, including recordings longer
    # than the planning limit previously used by the manual Longo form.
    if not (15 <= duration <= 180 if fmt == "Short" else duration >= 1): raise ValueError("Duração fora do limite do formato.")
