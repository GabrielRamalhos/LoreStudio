from app.services.subtitles import duration_wav
from app.services.narration import normalize_text

class VoiceService:
    def __init__(self, provider):
        self.provider = provider

    def generate(self, project, directory, cancel=None, *, text=None, speed=1.0, sentence_pause=0.3):
        narration = normalize_text(text if text is not None else project.script)
        output = self.provider.synthesize(narration, directory / 'narracao.wav', cancel, speed=speed, sentence_pause=sentence_pause)
        project.audio_duration = duration_wav(output)
        return output
