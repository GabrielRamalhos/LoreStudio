from app.core.models import Project
from app.database.repository import ProjectRepository
from app.services.long_video import deterministic_segments, persist_child_projects


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
