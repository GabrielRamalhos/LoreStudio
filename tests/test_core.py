import sys
import types
import wave
from pathlib import Path
import pytest
from app.core.models import Project, StepStatus, validate
from app.database.repository import ProjectRepository
from app.services.subtitles import ASRConfigurationError, CaptionAlignmentError, alignment_metadata, apply_word_adjustments, write_srt, write_word_srt, _repair_zero_length_word_intervals
from app.services.video import ffmpeg_command
from app.providers.piper import PiperProvider
from app.services.images import plan_scenes
from app.services.automation import create_batch, derive_shorts
from app.services.settings import Settings, SettingsStore, resolve_local_tools, resolved_mapping
from app.services.narration import normalize_text, speech_text
from app.services.voice import VoiceService
from app.services.hook_analysis import opening_excerpt, validate_response

def test_validates_short_limits():
    validate('Tema', 45, 'Short')
    with pytest.raises(ValueError): validate('',45,'Short')
    with pytest.raises(ValueError): validate('Tema',181,'Short')

def test_project_is_persisted(tmp_path):
    repo=ProjectRepository(tmp_path/'db.sqlite',tmp_path/'projects')
    saved=repo.save(Project(None,'','Tema','Filmes','Short',45,'Texto'))
    loaded=repo.get(saved.id)
    assert loaded.script == 'Texto'
    assert (tmp_path/'projects'/saved.slug/'roteiro.txt').read_text(encoding='utf8') == 'Texto'

def test_srt_uses_measured_wav_duration(tmp_path):
    audio=tmp_path/'a.wav'
    with wave.open(str(audio),'wb') as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(1000); w.writeframes(b'\0\0'*2000)
    target=write_srt('Uma frase. Outra frase.',audio,tmp_path/'out.srt')
    assert '00:00:02,000' in target.read_text(encoding='utf8')

def test_word_srt_uses_local_word_timestamps_without_downloading_models(tmp_path, monkeypatch):
    audio = tmp_path / 'a.wav'
    with wave.open(str(audio), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(1000); w.writeframes(b'\0\0' * 2000)
    created = []
    class Model:
        def __init__(self, name, **options):
            created.append((name, options))
        def transcribe(self, path, **options):
            assert options == {'language': 'pt', 'word_timestamps': True, 'vad_filter': True}
            words = [
                types.SimpleNamespace(start=0.0, end=.25, word='Olá'),
                types.SimpleNamespace(start=.25, end=.7, word='mundo.'),
                types.SimpleNamespace(start=.8, end=1.15, word='Tudo'),
                types.SimpleNamespace(start=1.15, end=1.5, word='bem?'),
            ]
            return iter([types.SimpleNamespace(words=words)]), object()
    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr('app.services.subtitles._decode_wav_for_asr', lambda _wav: 'pcm-local')
    target = write_word_srt(audio, tmp_path / 'out.srt', 'models/asr-pt')
    assert created == [('models/asr-pt', {'device': 'auto', 'compute_type': 'int8'})]
    assert target.read_text(encoding='utf8') == '1\n00:00:00,000 --> 00:00:00,700\nOlá mundo.\n\n2\n00:00:00,800 --> 00:00:01,500\nTudo bem?\n'
    assert target.with_suffix('.ass').is_file()

def test_word_srt_reports_an_unavailable_local_model_without_estimated_fallback(tmp_path, monkeypatch):
    audio = tmp_path / 'a.wav'
    with wave.open(str(audio), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(1000); w.writeframes(b'\0\0' * 1000)
    class MissingModel:
        def __init__(self, *_args, **_kwargs):
            raise OSError('not found')
    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=MissingModel))
    target = tmp_path / 'out.srt'
    target.write_text('anterior', encoding='utf8')
    with pytest.raises(ASRConfigurationError, match='Não foi possível preparar o modelo ASR local'):
        write_word_srt(audio, target, 'ausente')
    assert not target.exists()
    assert not target.with_suffix('.ass').exists()


def test_word_caption_refuses_zero_length_asr_token_instead_of_estimating_it():
    words = [types.SimpleNamespace(start=0.0, end=0.0, word='Olá'),
             types.SimpleNamespace(start=.2, end=.5, word='mundo')]
    with pytest.raises(CaptionAlignmentError, match='não retornou timestamps reais'):
        _repair_zero_length_word_intervals(words, 1.0)


def test_word_caption_repairs_degenerate_token_inside_observed_boundary_gap():
    words = [
        types.SimpleNamespace(start=0.0, end=.45, word='uma'),
        types.SimpleNamespace(start=.53, end=.53, word='a'),
        types.SimpleNamespace(start=.53, end=.9, word='historia'),
    ]
    aligned = _repair_zero_length_word_intervals(words, 1.0)
    assert aligned[1].timing_repaired is True
    assert .45 <= aligned[1].start < aligned[1].end == .53
    assert aligned[2].timing_repaired is False


def test_word_caption_repairs_an_isolated_quantized_boundary_collision():
    words = [
        types.SimpleNamespace(start=1.0, end=1.2, word='a'),
        types.SimpleNamespace(start=1.2, end=1.2, word='visão'),
        types.SimpleNamespace(start=1.2, end=1.5, word='chegou'),
    ]
    aligned = _repair_zero_length_word_intervals(words, 2.0)
    assert aligned[1].timing_repaired is True
    assert aligned[1].start == pytest.approx(1.2)
    assert aligned[1].end == pytest.approx(1.28)
    assert aligned[2].timing_repaired is False


def test_word_caption_persists_real_alignment_confidence_when_available(tmp_path, monkeypatch):
    audio = tmp_path / 'a.wav'
    with wave.open(str(audio), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(1000); w.writeframes(b'\0\0' * 1000)
    class Model:
        def __init__(self, *_args, **_kwargs): pass
        def transcribe(self, *_args, **_kwargs):
            words = [types.SimpleNamespace(start=0., end=.4, word='Olá', probability=.8),
                     types.SimpleNamespace(start=.4, end=1., word='mundo', probability=.6)]
            return iter([types.SimpleNamespace(words=words)]), object()
    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr('app.services.subtitles._decode_wav_for_asr', lambda _wav: 'pcm-local')
    target = write_word_srt(audio, tmp_path / 'out.srt')
    metadata = alignment_metadata(target)
    assert metadata['synchronized'] is True
    assert metadata['confidence'] == {'available': True, 'average': pytest.approx(.7), 'minimum': .6, 'threshold': .35}


def test_word_caption_refuses_low_alignment_confidence(tmp_path, monkeypatch):
    audio = tmp_path / 'a.wav'
    with wave.open(str(audio), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(1000); w.writeframes(b'\0\0' * 1000)
    class Model:
        def __init__(self, *_args, **_kwargs): pass
        def transcribe(self, *_args, **_kwargs):
            words = [types.SimpleNamespace(start=0., end=.5, word='Teste', probability=.1),
                     types.SimpleNamespace(start=.5, end=1., word='local', probability=.2)]
            return iter([types.SimpleNamespace(words=words)]), object()
    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr('app.services.subtitles._decode_wav_for_asr', lambda _wav: 'pcm-local')
    with pytest.raises(CaptionAlignmentError, match='baixa confiança'):
        write_word_srt(audio, tmp_path / 'out.srt')


def test_manual_word_adjustment_rewrites_ass_and_keeps_real_alignment_metadata(tmp_path, monkeypatch):
    audio = tmp_path / 'a.wav'
    with wave.open(str(audio), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(1000); w.writeframes(b'\0\0' * 1000)
    class Model:
        def __init__(self, *_args, **_kwargs): pass
        def transcribe(self, *_args, **_kwargs):
            words = [types.SimpleNamespace(start=0., end=.4, word='Olá', probability=.8),
                     types.SimpleNamespace(start=.4, end=1., word='mundo', probability=.6)]
            return iter([types.SimpleNamespace(words=words)]), object()
    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr('app.services.subtitles._decode_wav_for_asr', lambda _wav: 'pcm-local')
    target = write_word_srt(audio, tmp_path / 'out.srt')
    record = apply_word_adjustments(target, [{'index': 0, 'start': .05, 'end': .45}])
    assert record['manual_adjustments'] is True
    assert record['words'][0]['start'] == .05
    assert target.with_suffix('.ass').read_text(encoding='utf8').count('Dialogue:') == 2

def test_ffmpeg_command_is_vertical(tmp_path):
    command=ffmpeg_command('ffmpeg',tmp_path/'a.png',tmp_path/'a.wav',tmp_path/'a.srt',tmp_path/'o.mp4')
    assert 'scale=1080:1920' in command[command.index('-vf')+1]

def test_piper_refuses_missing_program(tmp_path):
    with pytest.raises(FileNotFoundError):
        PiperProvider(str(tmp_path/'piper.exe'),str(tmp_path/'voice.onnx')).synthesize('texto',tmp_path/'out.wav')

def test_scene_prompts_are_original_guidance():
    scenes=plan_scenes('Primeira cena. Segunda cena.')
    assert len(scenes)==2 and 'sem personagens protegidos' in scenes[0].prompt

def test_batch_and_short_derivation(tmp_path):
    repo=ProjectRepository(tmp_path/'db.sqlite',tmp_path/'projects')
    assert len(create_batch(repo,['A','B']))==2
    assert len(derive_shorts('Uma frase curta. Outra frase curta.',20))==2

def test_settings_round_trip(tmp_path):
    store=SettingsStore(tmp_path/'config.json'); store.save(Settings(ffmpeg=sys.executable))
    assert store.load().ffmpeg == sys.executable

def test_local_defaults_do_not_adopt_bundled_piper_or_voice(tmp_path):
    executable = tmp_path/'.venv'/'Scripts'/'piper.exe'
    executable.parent.mkdir(parents=True); executable.write_bytes(b'')
    voice = tmp_path/'models'/'piper'/'pt_BR.onnx'
    voice.parent.mkdir(parents=True); voice.write_bytes(b'voice')
    Path(str(voice)+'.json').write_text('{}', encoding='utf8')
    settings = resolve_local_tools(Settings(ffmpeg=sys.executable), tmp_path)
    assert settings.piper_executable == ''
    assert settings.piper_model == ''
    assert settings.ffmpeg == sys.executable

def test_local_defaults_do_not_select_piper_voice_when_multiple_voices_exist(tmp_path):
    voice_dir = tmp_path/'models'/'piper'
    voice_dir.mkdir(parents=True)
    for name in ('pt_BR-faber-medium.onnx', 'pt_BR-cadu-medium.onnx'):
        voice = voice_dir/name
        voice.write_bytes(b'voice')
        Path(str(voice)+'.json').write_text('{}', encoding='utf8')
    settings = resolve_local_tools(Settings(ffmpeg=sys.executable), tmp_path)
    assert settings.piper_model == ''

def test_resolved_settings_keep_project_finishing_values():
    settings = resolved_mapping({'ffmpeg': sys.executable, 'video_motion': True, 'video_music_volume': 22})
    assert settings['video_motion'] is True
    assert settings['video_music_volume'] == 22


def test_resolved_settings_supply_ffmpeg_when_a_legacy_project_left_it_empty():
    settings = resolved_mapping({'ffmpeg': ''})
    assert settings['ffmpeg']

def test_narration_normalizes_markup_entities_and_pronunciation():
    text = '## **Hal Jordan** &amp; <em>DC</em>\n[fonte](https://exemplo.com) `teste`\x00'
    assert normalize_text(text, {'Hal': 'Errado', 'Hal Jordan': 'Rél Djórdan'}) == 'Rél Djórdan e DC fonte teste'

def test_narration_keeps_legitimate_text_inside_code_block_and_removes_url():
    assert normalize_text('```Nome legítimo``` https://exemplo.com [fonte][1]') == 'Nome legítimo fonte'


def test_speech_only_phonetic_hint_preserves_superman_in_the_saved_script():
    assert speech_text('Superman enfrenta Batman.') == 'Supermen enfrenta Batman.'

def test_voice_generation_never_rewrites_the_saved_script(tmp_path):
    class Provider:
        def synthesize(self, text, output, cancel=None, **kwargs):
            output.write_bytes(b'fake')
            self.text = text
            return output
    project = Project(None, '', 'Tema', 'Games', 'Short', 45, 'Hal Jordan &amp; DC')
    provider = Provider()
    from unittest.mock import patch
    with patch('app.services.voice.duration_wav', return_value=1.0):
        VoiceService(provider).generate(project, tmp_path)
    assert project.script == 'Hal Jordan &amp; DC'
    assert provider.text == 'Hal Jordan e DC'


def test_hook_analysis_only_accepts_structured_non_invented_suggestions():
    script = 'Batman enfrenta o Coringa em Gotham e tenta salvar a cidade.'
    assert opening_excerpt(script) == script
    result = validate_response({'suggestions': [
        {'text': 'Batman enfrenta o Coringa em Gotham!', 'angle': 'conflito', 'reason': 'Abre com o conflito já citado.'},
    ]}, script)
    assert result[0]['angle'] == 'conflito'
    with pytest.raises(ValueError, match='introduziu referências'):
        validate_response({'suggestions': [
            {'text': 'Batman enfrenta Thanos!', 'angle': 'conflito', 'reason': 'Erro.'},
        ]}, script)
