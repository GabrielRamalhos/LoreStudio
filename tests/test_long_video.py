from app.core.models import Project
from app.database.repository import ProjectRepository
from app.services.long_video import deterministic_segments, persist_child_projects, source_short_command


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
