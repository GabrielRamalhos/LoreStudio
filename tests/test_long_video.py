from pathlib import Path

from app.core.models import Project
from app.database.repository import ProjectRepository
from app.services.long_video import (
    create_shorts_from_long_video,
    deterministic_segments,
    persist_child_projects,
    select_editorial_segments,
    source_short_command,
)
from app.services.projects import ProjectService


def words():
    values = []
    for index in range(90):
        values.append({'start': index * .75, 'end': index * .75 + .5, 'text': f'palavra{index}' + ('.' if index % 12 == 11 else '')})
    return values


def test_deterministic_segments_are_non_overlapping_and_use_real_word_boundaries():
    planned = deterministic_segments(words(), count=3, minimum_seconds=8, maximum_seconds=12)
    assert 1 <= len(planned) <= 3
    assert all(item.end > item.start and 8 <= item.end - item.start <= 12.5 for item in planned)
    assert all(left.end <= right.start for left, right in zip(planned, planned[1:]))
    assert all(item.script.startswith('palavra') for item in planned)


def test_child_projects_are_isolated_and_keep_source_ranges(tmp_path):
    repo = ProjectRepository(tmp_path / 'db.sqlite', tmp_path / 'projects')
    parent = repo.save(Project(None, '', 'Vídeo longo', 'Filmes', 'Longo', 300, ''))
    parent.settings['long_video_source'] = 'fonte/original.mp4'
    repo.save(parent)
    planned = deterministic_segments(words(), count=2, minimum_seconds=8, maximum_seconds=12)

    children = persist_child_projects(repo, parent, planned)

    assert len(children) == len(planned)
    assert all(item.id != parent.id and item.settings['source_short']['parent_project_id'] == parent.id for item in children)
    assert all(item.settings['source_short']['source'] == 'fonte/original.mp4' for item in children)
    assert repo.get(parent.id).settings['long_video_source'] == 'fonte/original.mp4'
    repo.cx.close()


def test_source_short_command_uses_vertical_composition_original_audio_and_ass(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    captions = tmp_path / 'legendas.ass'
    monkeypatch.setattr('app.services.long_video.executable', lambda _: 'ffmpeg')

    command = source_short_command('ffmpeg', source, captions, tmp_path / 'short.mp4', start=12.5, end=42.5)

    graph = command[command.index('-filter_complex') + 1]
    assert '-ss' in command and command[command.index('-ss') + 1] == '12.500'
    assert 'boxblur=20:10' in graph and 'ass=filename=' in graph
    assert '-map' in command and '0:a:0?' in command
    assert command[-1].endswith('short.mp4')


class _Director:
    def complete_structured(self, _prompt, _schema):
        return {'selected': [
            {'index': 1, 'title': 'Segundo ponto', 'score': 91},
            {'index': 0, 'title': 'Primeiro ponto', 'score': 87},
        ]}


def test_local_director_can_only_select_existing_ranges():
    candidates = deterministic_segments(words(), count=3, minimum_seconds=8, maximum_seconds=12)
    selected = select_editorial_segments(_Director(), candidates, count=2)

    assert [item.title for item in selected] == ['Primeiro Ponto', 'Segundo Ponto']
    assert {(item.start, item.end) for item in selected} <= {(item.start, item.end) for item in candidates}


def test_complete_batch_preserves_successful_children_when_one_render_fails(tmp_path, monkeypatch):
    repo = ProjectRepository(tmp_path / 'db.sqlite', tmp_path / 'projects')
    service = ProjectService(repo)
    source = tmp_path / 'origem.mp4'
    source.write_bytes(b'video')
    monkeypatch.setattr('app.services.long_video.source_video_duration', lambda *_: 400.0)

    def fake_import(service, batch, _source):
        target = service.artifact(batch, 'fonte/original.mp4')
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b'video')
        batch.settings['long_video_source'] = 'fonte/original.mp4'
        return target

    monkeypatch.setattr('app.services.long_video.import_source_video', fake_import)
    monkeypatch.setattr('app.services.long_video.extract_source_audio', lambda _ffmpeg, _source, target, _cancel: Path(target))
    long_words = [
        {'start': index * .75, 'end': index * .75 + .5, 'text': f'fala{index}' + ('.' if index % 12 == 11 else '')}
        for index in range(240)
    ]
    monkeypatch.setattr('app.services.long_video.transcribe_source_audio', lambda *_: long_words)
    rendered = []

    def fake_render(_service, child, _ffmpeg, _cancel):
        rendered.append(child.id)
        if len(rendered) == 2:
            raise RuntimeError('falha isolada')
        target = _service.artifact(child, 'video_final.mp4')
        target.write_bytes(b'mp4')
        child.statuses['video'] = 'CONCLUIDO'
        _service.repo.save(child)
        return target

    monkeypatch.setattr('app.services.long_video.render_child_short', fake_render)
    batch, children = create_shorts_from_long_video(service, source=source, ffmpeg='ffmpeg', asr_model='small', count=3)

    assert batch.settings['long_video_planning']['mode'] == 'deterministic-local'
    assert len(children) == 3
    assert [repo.get(item.id).statuses['video'] for item in children].count('CONCLUIDO') == 2
    assert [repo.get(item.id).statuses['video'] for item in children].count('ERRO') == 1
    manifest = service.artifact(batch, 'shorts-planejados.json').read_text(encoding='utf8')
    assert 'falha isolada' in manifest
    repo.cx.close()
