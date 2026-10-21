"""Investigação reproduzível do Piper PT-BR, sem gerar vídeo."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.providers.piper import PiperProvider
from app.services.narration import normalize_text
from app.services.voice import VoiceService

TEXT = 'Olá, este é um teste de narração em português brasileiro. Hal Jordan é um dos Lanternas Verdes mais poderosos.'

@dataclass
class Project:
    script: str = TEXT
    audio_duration: float | None = None

def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def main() -> None:
    root = Path('verification/piper-investigacao'); root.mkdir(parents=True, exist_ok=True)
    executable = Path('.venv/Scripts/piper.exe').resolve()
    model = Path('models/piper/pt_BR-cadu-medium.onnx').resolve()
    normalized = normalize_text(TEXT)
    provider = PiperProvider(str(executable), str(model))
    direct = provider.synthesize(normalized, root / 'teste_cadu_direto.wav')
    pipeline = VoiceService(provider).generate(Project(), root, text=normalized)
    (root / 'teste_cadu_pipeline.wav').write_bytes(pipeline.read_bytes())
    real = (root / 'narracao.piper-input.utf8.txt').read_bytes()
    report = {
        'original': TEXT, 'normalized': normalized, 'stdin_utf8': real.decode('utf8'),
        'stdin_sha256': digest(real), 'utf8_roundtrip': real == normalized.encode('utf8'),
        'model': str(model), 'model_json': json.loads(Path(str(model)+'.json').read_text(encoding='utf8')),
        'direct_wav': str(direct.resolve()), 'pipeline_wav': str((root/'teste_cadu_pipeline.wav').resolve()),
    }
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')

if __name__ == '__main__': main()
