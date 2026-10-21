from pathlib import Path
from typing import Protocol
from app.providers.process import executable, run_process
from app.services.subtitles import duration_wav

class TTSPort(Protocol):
    def synthesize(self, text: str, output: Path, cancel=None, *, speed=1.0, sentence_pause=0.3) -> Path: ...

class PiperProvider:
    def __init__(self, executable: str, model: str):
        self.executable, self.model = executable, model

    def synthesize(self, text: str, output: Path, cancel=None, *, speed=1.0, sentence_pause=0.3) -> Path:
        if not text.strip():
            raise ValueError('Roteiro vazio.')
        if not 0.6 <= float(speed) <= 1.5 or not 0 <= float(sentence_pause) <= 2:
            raise ValueError('Velocidade ou pausa de narração inválida.')
        program = executable(self.executable)
        model = Path(self.model)
        if not model.is_file() or not Path(str(model) + '.json').is_file():
            raise FileNotFoundError('Modelo Piper .onnx e configuração .onnx.json são obrigatórios.')
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.stem + '.working.wav')
        capture = output.with_name(output.stem + '.piper-input.utf8.txt')
        temporary.unlink(missing_ok=True)
        try:
            # A entrada é salva em bytes UTF-8 exatamente como será escrita no stdin.
            capture.write_bytes(text.encode('utf8'))
            command = [program, '--model', str(model.resolve()), '--output_file', str(temporary.resolve()), '--length-scale', str(1 / float(speed)), '--sentence-silence', str(float(sentence_pause))]
            run_process(command, cancel, text, output.parent / 'piper.log')
            duration_wav(temporary)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return output
