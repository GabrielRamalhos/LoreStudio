from pathlib import Path
import sys

from app.providers.process import run_process
from app.services.subtitles import duration_wav
from app.services.paths import resource_root


class KokoroProvider:
    """Adaptador do runtime local Kokoro (Python 3.12), separado da aplicação Python 3.14."""

    def __init__(self, python: str, voice: str = "pm_alex"):
        self.python = python
        self.voice = voice

    def synthesize(self, text: str, output: Path, cancel=None, *, speed=1.0, sentence_pause=0.3) -> Path:
        if not text.strip():
            raise ValueError("Roteiro vazio.")
        if self.voice not in {"pm_alex", "pm_santa", "pf_dora"}:
            raise ValueError("Voz Kokoro inválida.")
        if not 0.6 <= float(speed) <= 1.5:
            raise ValueError("Velocidade de narração inválida.")
        interpreter = Path(self.python)
        if not interpreter.is_file():
            raise FileNotFoundError("Runtime Kokoro não encontrado. Execute a instalação local do Kokoro.")
        output.parent.mkdir(parents=True, exist_ok=True)
        source = output.with_name(output.stem + ".kokoro.txt")
        working = output.with_name(output.stem + ".kokoro.working.wav")
        source.write_text(text, encoding="utf8")
        working.unlink(missing_ok=True)
        try:
            script = resource_root() / "scripts" / "kokoro_synthesize.py"
            command = [str(interpreter), str(script), "--input", str(source), "--output", str(working), "--voice", self.voice, "--speed", str(float(speed))]
            run_process(command, cancel, log=output.parent / "kokoro.log")
            duration_wav(working)
            working.replace(output)
        finally:
            source.unlink(missing_ok=True)
            working.unlink(missing_ok=True)
        return output
