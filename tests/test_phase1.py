import json
import os
import re
import subprocess
import sys
import threading
import tempfile
import wave
from pathlib import Path
import pytest
import imageio_ffmpeg
from app.core.models import Project, StepStatus
from app.database.repository import ProjectRepository
from app.services.projects import ProjectService
from app.services.pipeline import Pipeline
from app.services.subtitles import duration_wav, write_srt
from app.services.ass import write_ass
from app.services.video import render
from app.services.video import styled_command
from app.services.music import build_mix_plan, copy_music, detect_beats, load_effect_catalog, copy_effect_catalog
from app.services.timeline import legacy_timeline
from app.providers.piper import PiperProvider
from app.providers.process import run_process, ProviderError

def wav(path, seconds=1):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * int(16000 * seconds))
    return path

def repository(tmp_path):
    return ProjectRepository(tmp_path / 'db.sqlite', tmp_path / 'projects')

def test_duplicate_theme_keeps_independent_artifacts(tmp_path):
    repo = repository(tmp_path)
    first = repo.save(Project(None, '', 'Mesmo tema', 'Games', 'Short', 45, 'Primeiro.'))
    second = repo.save(Project(None, '', 'Mesmo tema', 'Games', 'Short', 45, 'Segundo.'))
    assert first.slug != second.slug
    assert (repo.root / first.slug / 'roteiro.txt').read_text(encoding='utf8') == 'Primeiro.'
    assert repo.get(second.id).script == 'Segundo.'
    repo.cx.close()

def test_invalid_project_does_not_create_record_or_folder(tmp_path):
    repo = repository(tmp_path)
    with pytest.raises(ValueError, match='Categoria'):
        repo.save(Project(None, '', 'Tema', '', 'Short', 45))
    assert repo.list() == []
    assert list(repo.root.iterdir()) == []
    repo.cx.close()

def test_reopen_marks_missing_and_interrupted_artifacts_recoverable(tmp_path):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    project.settings = {'piper_model': 'voice.onnx'}
    project.statuses = {'voz': 'CONCLUIDO', 'video': 'PROCESSANDO'}
    repo.save(project)
    loaded = ProjectService(repo).reopen(project.id)
    assert loaded.statuses == {'voz': 'ERRO', 'video': 'ERRO'}
    assert len(loaded.history) == 2
    assert loaded.settings == project.settings
    repo.cx.close()

def test_imported_images_are_relative_and_survive_same_filename(tmp_path):
    from PySide6.QtGui import QImage
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45))
    source = tmp_path / 'imagem.png'
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xffaa0000)
    assert image.save(str(source))
    service = ProjectService(repo)
    service.add_images(project, [str(source), str(source)])
    assert len(set(project.images)) == 2
    assert all(not Path(name).is_absolute() and service.artifact(project, name).is_file() for name in project.images)
    assert repo.get(project.id).images == project.images
    repo.cx.close()

def test_import_long_filename_uses_short_internal_copy(tmp_path):
    from PySide6.QtGui import QImage
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45))
    with tempfile.TemporaryDirectory() as source_root:
        source = Path(source_root) / ('a' * 192 + '.jpg')
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(0xffaa0000)
        assert image.save(str(source))
        service = ProjectService(repo)
        service.add_images(project, [str(source)])
        copied = service.artifact(project, project.images[0])
        assert copied.is_file()
        assert copied.suffix == '.jpg'
        assert copied.name != source.name
        assert len(str(copied)) < 240
    repo.cx.close()


def test_import_jfif_preserves_the_supported_jpeg_extension(tmp_path):
    from PySide6.QtGui import QImage
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45))
    source = tmp_path / 'imagem.jfif'
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xffaa0000)
    assert image.save(str(source), 'JPG')
    service = ProjectService(repo)
    service.add_images(project, [str(source)])
    copied = service.artifact(project, project.images[0])
    assert copied.is_file()
    assert copied.suffix == '.jfif'
    repo.cx.close()

def test_import_race_reports_source_and_preserves_prior_images(tmp_path, monkeypatch):
    from PySide6.QtGui import QImage
    import app.services.projects as module
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45))
    first, missing = tmp_path / 'primeira.png', tmp_path / 'sumiu.png'
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xffaa0000)
    assert image.save(str(first)) and image.save(str(missing))
    existing = tmp_path / 'existente.png'
    assert image.save(str(existing))
    service = ProjectService(repo)
    service.add_images(project, [str(existing)])
    before = list(project.images)
    actual = module.shutil.copy2
    def copy_with_race(source, target):
        if Path(source) == missing:
            raise FileNotFoundError(3, 'O sistema não pode encontrar o caminho especificado', str(source))
        return actual(source, target)
    monkeypatch.setattr(module.shutil, 'copy2', copy_with_race)
    with pytest.raises(OSError, match='sumiu.png'):
        service.add_images(project, [str(first), str(missing)])
    restored = repo.get(project.id)
    assert len(restored.images) == 2
    assert restored.images[:1] == before
    assert all(service.artifact(restored, name).is_file() for name in restored.images)
    assert len(list((repo.root / project.slug / 'cenas').iterdir())) == 2
    assert not list((repo.root / project.slug / 'cenas').glob('*.part'))
    assert 'interrompida em sumiu.png' in restored.history[-1]['message']
    repo.cx.close()

def test_missing_source_before_copy_reports_filename_without_artifacts(tmp_path):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45))
    vanished = tmp_path / 'sumiu-antes.png'
    with pytest.raises(OSError, match='sumiu-antes.png'):
        ProjectService(repo).add_images(project, [str(vanished)])
    restored = repo.get(project.id)
    assert restored.images == []
    assert not list((repo.root / project.slug / 'cenas').glob('*.part'))
    repo.cx.close()

def test_import_save_failure_removes_new_files_and_references(tmp_path, monkeypatch):
    from PySide6.QtGui import QImage
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45))
    source = tmp_path / 'imagem.png'
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xffaa0000)
    assert image.save(str(source))
    def fail_save(_):
        raise OSError('SQLite temporariamente indisponível')
    monkeypatch.setattr(repo, 'save', fail_save)
    with pytest.raises(OSError, match='SQLite'):
        ProjectService(repo).add_images(project, [str(source)])
    assert project.images == []
    assert not list((repo.root / project.slug / 'cenas').iterdir())
    repo.cx.close()

def test_pipeline_provider_failure_persists_real_error(tmp_path):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    with pytest.raises(FileNotFoundError):
        Pipeline(ProjectService(repo)).execute(project, 'voz', {'piper_executable': 'missing-piper-123', 'piper_model': 'missing'}, threading.Event())
    loaded = repo.get(project.id)
    assert loaded.statuses['voz'] == 'ERRO'
    assert 'Executável não encontrado' in loaded.history[-1]['message']
    repo.cx.close()

def test_automatic_short_runs_existing_steps_in_order(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    project.images = ['cenas/imagem.png']
    project.statuses['imagens'] = StepStatus.DONE
    image = ProjectService(repo).artifact(project, project.images[0])
    image.parent.mkdir(exist_ok=True)
    image.write_bytes(b'image')
    calls = []
    pipeline = Pipeline(ProjectService(repo))
    def run_step(candidate, step, settings, cancel):
        calls.append(step)
        return tmp_path / ('video_final.mp4' if step == 'video' else step)
    monkeypatch.setattr(pipeline, 'execute', run_step)
    result = pipeline.execute_short(project, {}, threading.Event())
    assert calls == ['voz', 'legendas', 'video']
    assert result.name == 'video_final.mp4'
    repo.cx.close()

def test_automatic_short_requires_script_and_images(tmp_path):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, ''))
    with pytest.raises(ValueError, match='roteiro'):
        Pipeline(ProjectService(repo)).execute_short(project, {}, threading.Event())
    project.script = 'Texto.'
    with pytest.raises(ValueError, match='imagem'):
        Pipeline(ProjectService(repo)).execute_short(project, {}, threading.Event())
    repo.cx.close()

def test_automatic_short_rejects_unavailable_imported_image(tmp_path):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    project.images = ['cenas/ausente.png']
    project.statuses['imagens'] = StepStatus.DONE
    with pytest.raises(ValueError, match='não está mais disponível'):
        Pipeline(ProjectService(repo)).execute_short(project, {}, threading.Event())
    repo.cx.close()

def test_empty_audio_refuses_subtitles_without_overwriting(tmp_path):
    audio = wav(tmp_path / 'empty.wav', 0)
    target = tmp_path / 'legendas.srt'
    target.write_text('original', encoding='utf8')
    with pytest.raises(ValueError, match='WAV vazio'):
        write_srt('Uma frase.', audio, target)
    assert target.read_text() == 'original'

def test_pipeline_generates_subtitles_with_local_word_asr(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    project.statuses['voz'] = StepStatus.DONE
    audio = wav(ProjectService(repo).artifact(project, 'narracao.wav'))
    calls = []
    def word_srt(source, target, model, style='standard'):
        calls.append((source, target, model, style))
        target.write_text('1\n00:00:00,000 --> 00:00:00,500\nTexto.\n', encoding='utf8')
        target.with_suffix('.ass').write_text(
            '[Events]\nDialogue: 0,0:00:00.00,0:00:00.50,Default,,0,0,0,,Texto.\n',
            encoding='utf8',
        )
        target.with_suffix('.words.json').write_text('{"synchronized": true}', encoding='utf8')
        return target
    monkeypatch.setattr('app.services.pipeline.write_word_srt', word_srt)
    target = Pipeline(ProjectService(repo)).execute(project, 'legendas', {'asr_model': 'models/pt'}, threading.Event())
    assert target.read_text(encoding='utf8').startswith('1\n')
    assert calls == [(audio, target, 'models/pt', 'standard')]
    assert project.statuses['legendas'] == StepStatus.DONE
    repo.cx.close()


def test_video_automatically_rebuilds_missing_word_captions_before_export(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    project.images = ['cenas/one.png']
    project.statuses.update(imagens=StepStatus.DONE, voz=StepStatus.DONE, legendas=StepStatus.DONE)
    service = ProjectService(repo)
    image = service.artifact(project, 'cenas/one.png')
    image.parent.mkdir(exist_ok=True)
    image.write_bytes(b'fixture')
    wav(service.artifact(project, 'narracao.wav'))
    generated = []
    def word_srt(source, target, model, style='standard'):
        generated.append((source, target, model, style))
        target.write_text('1\n00:00:00,000 --> 00:00:01,000\nTexto.\n', encoding='utf8')
        target.with_suffix('.ass').write_text('[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Texto.\n', encoding='utf8')
        target.with_suffix('.words.json').write_text('{"synchronized": true}', encoding='utf8')
        return target
    monkeypatch.setattr('app.services.pipeline.write_word_srt', word_srt)
    monkeypatch.setattr('app.services.pipeline.render', lambda *_args, **_kwargs: service.artifact(project, 'video_final.mp4'))
    result = Pipeline(service).execute(project, 'video', {'ffmpeg': sys.executable}, threading.Event())
    assert result.name == 'video_final.mp4'
    assert generated and generated[0][0].name == 'narracao.wav'
    assert project.settings['caption_timing'] == 'word'
    assert 'automaticamente' in project.history[-2]['message']
    repo.cx.close()

def test_process_records_stderr_and_cancellation(tmp_path):
    log = tmp_path / 'process.json'
    with pytest.raises(ProviderError, match='falha real'):
        run_process([sys.executable, '-c', 'import sys; sys.stderr.write("falha real"); sys.exit(3)'], log=log)
    assert json.loads(log.read_text(encoding='utf8'))['returncode'] == 3
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ProviderError, match='cancelada'):
        run_process([sys.executable, '-c', 'import time; time.sleep(30)'], cancel)

def test_piper_invalid_success_preserves_previous_wav(tmp_path, monkeypatch):
    import app.providers.piper as module
    model = tmp_path / 'voice.onnx'
    model.write_bytes(b'model')
    Path(str(model) + '.json').write_text('{}')
    output = wav(tmp_path / 'narracao.wav')
    original = output.read_bytes()
    monkeypatch.setattr(module, 'run_process', lambda *args, **kwargs: None)
    with pytest.raises(FileNotFoundError):
        PiperProvider(sys.executable, str(model)).synthesize('Teste.', output)
    assert output.read_bytes() == original

def test_piper_accepts_only_validated_wav(tmp_path, monkeypatch):
    import app.providers.piper as module
    model = tmp_path / 'voice.onnx'
    model.write_bytes(b'model')
    Path(str(model) + '.json').write_text('{}')
    def process(command, *args):
        wav(Path(command[command.index('--output_file') + 1]), .5)
    monkeypatch.setattr(module, 'run_process', process)
    result = PiperProvider(sys.executable, str(model)).synthesize('Teste.', tmp_path / 'narracao.wav')
    assert duration_wav(result) == .5
    assert not (tmp_path / 'narracao.working.wav').exists()

def test_real_vertical_video_with_two_images_and_unicode_path(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    directory = tmp_path / "produção com espaços e 'aspas'"
    directory.mkdir()
    images = []
    for index, color in enumerate((b'\xff\0\0', b'\0\0\xff')):
        path = directory / f'imagem {index}.ppm'
        path.write_bytes(b'P6\n32 32\n255\n' + color * 32 * 32)
        images.append(path)
    audio = wav(directory / 'voz.wav', 1.2)
    subtitles = write_srt('Uma frase. Outra frase.', audio, directory / 'legendas.srt')
    output = render(ffmpeg, images, audio, subtitles, directory / 'video_final.mp4')
    assert output.stat().st_size > 1000
    info = subprocess.run([ffmpeg, '-hide_banner', '-i', str(output), '-f', 'null', '-'], capture_output=True, text=True, encoding='utf8', errors='replace')
    assert info.returncode == 0, info.stderr
    assert '1080x1920' in info.stderr
    assert 'Audio: aac' in info.stderr
    match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', info.stderr)
    assert match
    actual = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    assert abs(actual - 1.2) < .15
    colors = []
    for timestamp in ('0.2', '0.9'):
        result = subprocess.run([ffmpeg, '-v', 'error', '-ss', timestamp, '-i', str(output), '-frames:v', '1', '-vf', 'crop=10:10:0:0,scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True)
        assert result.returncode == 0
        colors.append(result.stdout)
    assert colors[0][0] > 200 and colors[0][2] < 30
    assert colors[1][2] > 200 and colors[1][0] < 30
    original = output.read_bytes()
    with pytest.raises(ValueError, match='sincronizadas por palavra'):
        render(ffmpeg, images, audio, subtitles, output, require_word_captions=True)
    assert output.read_bytes() == original
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ProviderError, match='cancelada'):
        render(ffmpeg, images, audio, subtitles, output, cancel)
    assert output.read_bytes() == original
    assert (directory / 'ffmpeg.log').is_file()


def test_retention_preset_renders_six_scene_short_with_real_narration_duration(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    images = []
    for index, color in enumerate((b'\xff\0\0', b'\0\xff\0', b'\0\0\xff', b'\xff\xff\0', b'\xff\0\xff', b'\0\xff\xff')):
        path = tmp_path / f'cena-{index}.ppm'
        path.write_bytes(b'P6\n32 32\n255\n' + color * 32 * 32)
        images.append(path)
    narration = wav(tmp_path / 'narracao.wav', 30)
    subtitles = write_srt('Uma narracao local para validar o preset de retencao.', narration, tmp_path / 'legendas.srt')
    captions = write_ass([{'start': 0, 'end': 15, 'text': 'Uma'}, {'start': 15, 'end': 30, 'text': 'narracao'}], tmp_path / 'legendas.ass', style='retention')
    timeline = legacy_timeline([f'cenas/{index}.ppm' for index in range(6)], 30, 'Lore Oculta — Retenção Máxima', 30)
    output = render(ffmpeg, images, narration, subtitles, tmp_path / 'retencao.mp4', motion=True, transition=True,
                    fps=30, timeline=timeline, mix_profile='cinematografica', ass=captions, require_word_captions=True)
    assert output.is_file() and output.stat().st_size > 1000
    report = json.loads((tmp_path / 'technical_report.json').read_text(encoding='utf8'))
    assert report['valid'] is True
    assert report['retention_preset'] == {'active': True, 'image_count': 6, 'expected_images': 6}
    assert report['caption_timing'] == 'word'


def test_music_plan_uses_beats_and_safe_uniform_fallback():
    detected = build_mix_plan(9, 3, [3.05, 6.02])
    assert detected['beat_detection'] == 'detected'
    assert detected['cuts'] == [3.05, 6.02]
    assert sum(detected['scene_durations']) == pytest.approx(9)
    fallback = build_mix_plan(9, 3, [])
    assert fallback['beat_detection'] == 'fallback_uniform'
    assert fallback['scene_durations'] == [3, 3, 3]


def test_effect_catalog_requires_license_and_copies_project_owned_media(tmp_path):
    source = wav(tmp_path / 'impact.wav', .2)
    manifest = tmp_path / 'effects.json'
    manifest.write_text(json.dumps([{
        'intent': 'impact', 'title': 'Impacto curto', 'file': source.name, 'license': 'CC0',
    }]), encoding='utf8')
    catalog = load_effect_catalog(manifest)
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 30, 'Texto.'))
    effects = copy_effect_catalog(ProjectService(repo), project, catalog, imageio_ffmpeg.get_ffmpeg_exe())
    assert effects['impact']['license'] == 'CC0'
    assert ProjectService(repo).artifact(project, effects['impact']['path']).is_file()
    repo.cx.close()


def test_pipeline_binds_only_declared_licensed_effects_to_retention_events(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    service = ProjectService(repo)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 30, 'Texto.'))
    project.images = [f'cenas/{index}.png' for index in range(6)]
    project.statuses.update(imagens=StepStatus.DONE, voz=StepStatus.DONE, legendas=StepStatus.DONE)
    root = service.artifact_dir(project)
    wav(root / 'narracao.wav', 1)
    (root / 'legendas.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nTexto\n', encoding='utf8')
    (root / 'legendas.ass').write_text('[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Texto\n', encoding='utf8')
    (root / 'legendas.words.json').write_text('{"synchronized": true}', encoding='utf8')
    effect = root / 'efeitos' / 'impact.wav'
    effect.parent.mkdir(); effect.write_bytes(b'licensed-effect')
    captured = {}
    def fake_render(*_args, **kwargs):
        captured.update(kwargs)
        (root / 'technical_report.json').write_text(json.dumps({'audio_events': {}}), encoding='utf8')
        return root / 'video_final.mp4'
    monkeypatch.setattr('app.services.pipeline.render', fake_render)
    settings = {'editing_preset': 'Lore Oculta — Retenção Máxima', 'video_fps': 30,
                'effects_intensity': 'moderada',
                'licensed_effects': {'impact': {'path': 'efeitos/impact.wav', 'title': 'Impacto', 'license': 'CC0'}}}
    Pipeline(service).execute(project, 'video', settings, threading.Event())
    assert [event['path'] for event in captured['event_assets']] == [effect]
    manifest = json.loads((root / 'render_manifest.json').read_text(encoding='utf8'))
    assert manifest['sources'] == [{'path': 'efeitos/impact.wav', 'license': 'CC0', 'title': 'Impacto'}]
    assert manifest['technical_report']['audio_events']['omitted'] == ['suspense', 'climax']
    repo.cx.close()


def test_music_plan_ignores_malformed_persisted_beats():
    plan = build_mix_plan(6, 2, ['x', None, float('nan'), 3])
    assert plan['cuts'] == [3.0]


def test_detect_beats_finds_local_energy_onsets(tmp_path):
    import numpy as np
    source = tmp_path / 'onsets.wav'
    rate = 16000
    samples = np.zeros(rate * 3, dtype=np.int16)
    for start in (rate // 2, rate * 3 // 2):
        samples[start:start + rate // 12] = 24000
    with wave.open(str(source), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(rate)
        audio.writeframes(samples.tobytes())
    beats = detect_beats(imageio_ffmpeg.get_ffmpeg_exe(), source, 2)
    assert any(abs(beat - .5) < .15 for beat in beats)
    assert any(abs(beat - 1.5) < .15 for beat in beats)


def test_invalid_music_does_not_copy_or_change_project(tmp_path):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    project.images = ['cenas/imagem.png']
    ProjectService(repo).artifact(project, project.images[0]).parent.mkdir(exist_ok=True)
    ProjectService(repo).artifact(project, project.images[0]).write_bytes(b'image')
    wav(ProjectService(repo).artifact(project, 'narracao.wav'))
    project.settings['video_music'] = 'trilha/anterior.wav'
    broken = tmp_path / 'quebrada.wav'
    broken.write_bytes(b'not audio')
    with pytest.raises(ValueError, match='FFmpeg não conseguiu abrir'):
        copy_music(ProjectService(repo), project, broken, imageio_ffmpeg.get_ffmpeg_exe())
    assert project.settings['video_music'] == 'trilha/anterior.wav'
    assert not (ProjectService(repo).artifact_dir(project) / 'trilha').exists()
    repo.cx.close()


def test_valid_music_is_copied_into_project_with_a_persistent_plan(tmp_path):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    project.images = ['cenas/um.png', 'cenas/dois.png']
    wav(ProjectService(repo).artifact(project, 'narracao.wav'), 2)
    source = wav(tmp_path / 'trilha.wav', 3)
    internal, plan = copy_music(ProjectService(repo), project, source, imageio_ffmpeg.get_ffmpeg_exe())
    copied = ProjectService(repo).artifact(project, internal)
    assert copied.is_file()
    assert copied.read_bytes() == source.read_bytes()
    assert len(plan['scene_durations']) == len(project.images)
    project.settings.update(video_music=internal, video_music_plan=plan)
    repo.save(project)
    restored = repo.get(project.id)
    assert restored.settings['video_music'] == internal
    assert restored.settings['video_music_plan'] == plan
    repo.cx.close()


def test_music_render_command_ducks_normalizes_and_keeps_narration_duration(tmp_path):
    command = styled_command('ffmpeg', [tmp_path / 'one.png', tmp_path / 'two.png'], tmp_path / 'voice.wav',
                             tmp_path / 'out.mp4', 2, False, False, tmp_path / 'music.wav', .12,
                             {'scene_durations': [.8, 1.2]})
    filters = command[command.index('-filter_complex') + 1]
    assert 'sidechaincompress' in filters
    assert 'loudnorm=I=-24' in filters
    assert 'afade=t=in' in filters and 'atrim=duration=2.000000000' in filters
    assert 'trim=duration=0.800000000' in filters and 'trim=duration=1.200000000' in filters


def test_pipeline_forwards_project_music_and_rebuilds_stale_plan(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    project.images = ['cenas/imagem.png']
    project.statuses = {'voz': StepStatus.DONE, 'legendas': StepStatus.DONE, 'imagens': StepStatus.DONE}
    service = ProjectService(repo)
    narration = wav(service.artifact(project, 'narracao.wav'), 2)
    service.artifact(project, 'legendas.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nTexto\n', encoding='utf8')
    music = service.artifact(project, 'trilha/musica.wav')
    music.parent.mkdir(); wav(music)
    captured = {}
    def fake_render(*args, **kwargs):
        captured['music'] = kwargs['music']
        captured['plan'] = kwargs['music_plan']
        return args[4]
    monkeypatch.setattr('app.services.pipeline.render', fake_render)
    settings = {'ffmpeg': imageio_ffmpeg.get_ffmpeg_exe(), 'video_music': 'trilha/musica.wav',
                'video_music_plan': {'scene_durations': [1], 'beat_times': ['bad', 1]}}
    Pipeline(service).execute(project, 'video', settings, threading.Event())
    assert captured['music'] == music
    assert sum(captured['plan']['scene_durations']) == pytest.approx(duration_wav(narration))
    assert project.settings['video_music_plan'] == captured['plan']
    repo.cx.close()


def test_music_render_produces_aac_video_with_narration_duration(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    images = []
    for index, color in enumerate((b'\xff\0\0', b'\0\0\xff')):
        image = tmp_path / f'cena-{index}.ppm'
        image.write_bytes(b'P6\n16 16\n255\n' + color * 16 * 16)
        images.append(image)
    narration = wav(tmp_path / 'narracao.wav', 1)
    music = wav(tmp_path / 'trilha.wav', 2)
    subtitles = write_srt('Uma frase.', narration, tmp_path / 'legendas.srt')
    output = render(ffmpeg, images, narration, subtitles, tmp_path / 'video.mp4', music=music,
                    music_plan=build_mix_plan(1, 2, [.5]))
    info = subprocess.run([ffmpeg, '-hide_banner', '-i', str(output), '-f', 'null', '-'], capture_output=True,
                          text=True, encoding='utf8', errors='replace')
    assert info.returncode == 0, info.stderr
    assert 'Audio: aac' in info.stderr
    match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', info.stderr)
    assert match
    actual = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    assert abs(actual - 1) < .15
