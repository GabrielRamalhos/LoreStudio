import copy

import pytest

from app.services.editorial_director import fallback_plan, plan_hash, stable_input_hash, timeline_from_response
from app.services.timeline import adapt_project, validate_director_timeline
from app.core.models import Project


SOURCES = ['cenas/um.png', 'cenas/dois.png']
RESPONSE = {'clips': [
    {'source': 'cenas/um.png', 'start': 0, 'end': 2, 'motion': 'slow_push', 'transition': 'cut'},
    {'source': 'cenas/dois.png', 'start': 2, 'end': 5, 'motion': 'slow_pull', 'transition': 'fade'},
]}


def plan():
    return timeline_from_response(RESPONSE, script='Um roteiro.', sources=SOURCES, duration=5, model='local-model')


def test_director_plan_is_reproducible_and_has_provenance():
    first, second = plan(), plan()
    assert first['director']['input_hash'] == stable_input_hash('Um roteiro.', SOURCES, 5)
    assert first['director']['plan_hash'] == plan_hash(first) == second['director']['plan_hash']
    assert validate_director_timeline(first, SOURCES, 5) is first


@pytest.mark.parametrize('change', [
    lambda value: value['clips'][0].update(source='cenas/inventada.png'),
    lambda value: value['clips'].reverse(),
    lambda value: value['clips'][1].update(start=2.1),
    lambda value: value['clips'][-1].update(end=5.1),
    lambda value: value['clips'][0].update(extra='nope'),
])
def test_invalid_director_decisions_are_rejected(change):
    value = plan(); change(value)
    with pytest.raises(ValueError): validate_director_timeline(value, SOURCES, 5)


def test_visual_director_may_reorder_only_with_explicit_provenance():
    response = {'clips': [
        {'source': 'cenas/dois.png', 'start': 0, 'end': 2, 'motion': 'slow_push', 'transition': 'cut'},
        {'source': 'cenas/um.png', 'start': 2, 'end': 5, 'motion': 'slow_pull', 'transition': 'fade'},
    ]}
    visual = timeline_from_response(
        response, script='Um roteiro.', sources=SOURCES, duration=5,
        model='modelo-visual-local', visual_ordering=True,
    )
    assert [clip['source'] for clip in visual['clips']] == list(reversed(SOURCES))
    assert visual['director']['visual_ordering'] is True
    assert validate_director_timeline(visual, SOURCES, 5) is visual

    non_visual = timeline_from_response(
        response, script='Um roteiro.', sources=SOURCES, duration=5,
        model='modelo-local', visual_ordering=False,
    )
    with pytest.raises(ValueError, match='ordem importada'):
        validate_director_timeline(non_visual, SOURCES, 5)


def test_fallback_is_deterministic_and_keeps_import_order():
    assert fallback_plan(SOURCES, 5, 'neutral') == fallback_plan(SOURCES, 5, 'neutral')
    assert [clip['source'] for clip in fallback_plan(SOURCES, 5)['clips']] == SOURCES


def test_director_response_uses_exact_local_ranges_when_model_rounds_audio_duration():
    rounded = copy.deepcopy(RESPONSE)
    rounded['clips'][0].update(start=0, end=15)
    rounded['clips'][1].update(start=15, end=30)
    timeline = timeline_from_response(
        rounded, script='Um roteiro.', sources=SOURCES, duration=30.75, model='local-model',
    )
    assert [(clip['start'], clip['end']) for clip in timeline['clips']] == [(0.0, 15.375), (15.375, 30.75)]
    assert validate_director_timeline(timeline, SOURCES, 30.75) is timeline


def test_invalid_response_does_not_mutate_existing_plan():
    existing = plan(); preserved = copy.deepcopy(existing)
    with pytest.raises(ValueError):
        timeline_from_response({'clips': RESPONSE['clips'], 'invented': True}, script='Um roteiro.', sources=SOURCES, duration=5, model='local')
    assert existing == preserved


def test_narration_rescale_keeps_director_metadata_and_updates_fingerprint():
    project = Project(1, 'demo', 'Tema', 'Games', 'Short', 30, images=SOURCES)
    project.settings['timeline'] = plan()
    original = project.settings['timeline']['director'].copy()
    adapted = adapt_project(project, 10)
    assert adapted['director']['model'] == original['model']
    assert adapted['director']['input_hash'] == original['input_hash']
    assert adapted['director']['plan_hash'] == plan_hash(adapted)
    assert adapted['clips'][-1]['end'] == 10


def test_adapt_rejects_director_plan_when_project_media_changed():
    project = Project(1, 'demo', 'Tema', 'Games', 'Short', 30, images=list(reversed(SOURCES)))
    project.settings['timeline'] = plan()
    with pytest.raises(ValueError, match='ordem importada'):
        adapt_project(project, 5)
