import json
import shutil
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from app.services.paths import resource_root

PROJECT_ROOT = resource_root()
DEFAULT_COMFYUI_PROFILE = PROJECT_ROOT / 'app' / 'resources' / 'comfyui' / 'sd15-vertical-conservative.json'


def default_comfyui_workflow() -> str:
    """Perfil SD 1.5 9:16 de baixo consumo distribuído com o aplicativo."""
    try:
        return DEFAULT_COMFYUI_PROFILE.read_text(encoding='utf8')
    except OSError as error:
        raise RuntimeError('Perfil local padrão do ComfyUI não encontrado. Reinstale o aplicativo.') from error

@dataclass
class Settings:
    output_dir: str = ''
    ffmpeg: str = 'ffmpeg'
    ollama_model: str = 'llama3.2'
    ollama_url: str = 'http://localhost:11434'
    visual_analysis_enabled: bool = False
    visual_ollama_model: str = ''
    piper_executable: str = ''
    piper_model: str = ''
    export_quality: str = 'medium'
    pronunciation_dictionary: dict[str, str] = field(default_factory=dict)
    narration_speed: float = 1.0
    sentence_pause: float = 0.3
    tts_provider: str = 'kokoro'
    kokoro_python: str = ''
    kokoro_voice: str = 'pm_alex'
    asr_model: str = 'small'
    comfyui_url: str = 'http://127.0.0.1:8188'
    comfyui_workflow: str = ''
    comfyui_checkpoint: str = ''
    onboarding_complete: bool = False
    video_fps: int = 30
    editing_preset: str = 'neutral'
    music_mix_profile: str = ''
    effects_intensity: str = 'moderada'

def resolve_local_tools(settings: Settings, base: Path | None = None) -> Settings:
    """Completa somente padrões ausentes com recursos locais do projeto."""
    base = (base or PROJECT_ROOT).resolve()
    resolved = Settings(**asdict(settings))
    executable_available = Path(resolved.piper_executable).is_file() or bool(shutil.which(resolved.piper_executable)) if resolved.piper_executable else False
    if resolved.piper_executable and not executable_available:
        resolved.piper_executable = ''
    # A project can be moved after Kokoro was configured. Do not retain a
    # stale absolute interpreter path; discover the project-local runtime.
    if resolved.kokoro_python and not Path(resolved.kokoro_python).is_file():
        resolved.kokoro_python = ''
    if not resolved.kokoro_python:
        candidates = (base / 'vendor' / 'kokoro' / 'python.exe', base / '.kokoro-venv' / 'Scripts' / 'python.exe')
        candidate = next((item for item in candidates if item.is_file()), None)
        if candidate: resolved.kokoro_python = str(candidate)
    ffmpeg_available = Path(resolved.ffmpeg).is_file() or bool(shutil.which(resolved.ffmpeg)) if resolved.ffmpeg else False
    if not ffmpeg_available:
        bundled_ffmpeg = base / 'vendor' / 'ffmpeg' / 'ffmpeg.exe'
        if bundled_ffmpeg.is_file():
            resolved.ffmpeg = str(bundled_ffmpeg)
            return resolved
        try:
            import imageio_ffmpeg
            candidate = imageio_ffmpeg.get_ffmpeg_exe()
            # The development imageio binary may be GPL; it is never an installer input.
            if not getattr(sys, 'frozen', False):
                resolved.ffmpeg = candidate
        except Exception:
            pass
    return resolved


def resolve_comfyui_workflow(values: dict) -> str:
    """Mantém a escolha explícita do projeto; sem ela, usa o perfil versionado."""
    return values.get('comfyui_workflow', '').strip() or default_comfyui_workflow()

def resolved_mapping(values: dict, base: Path | None = None) -> dict:
    fields = set(Settings.__dataclass_fields__)
    resolved = asdict(resolve_local_tools(Settings(**{key: value for key, value in values.items() if key in fields}), base))
    return values | resolved

class SettingsStore:
    def __init__(self, path: Path): self.path=path
    def load(self) -> Settings:
        values = json.loads(self.path.read_text(encoding='utf8')) if self.path.is_file() else {}
        settings = resolve_local_tools(Settings(**values))
        if not self.path.is_file() or asdict(settings) != values:
            self.save(settings)
        return settings
    def save(self, settings: Settings):
        self.path.parent.mkdir(parents=True,exist_ok=True); self.path.write_text(json.dumps(asdict(settings),ensure_ascii=False,indent=2),encoding='utf8')
