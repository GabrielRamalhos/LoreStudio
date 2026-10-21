from pathlib import Path

import pytest

from app.core.models import Project
from app.services.timeline import align_timeline_to_words, adapt_project, invalidate_derived, scene_durations, validate_timeline, write_manifest
from app.services.editing_presets import validate_preset_images


def test_legacy_project_gets_reproducible_relative_timeline(tmp_path):
    project = Project(1, 'demo', 'Tema', 'Games', 'Short', 30, images=['cenas/um.png', 'cenas/dois.png'])
    timeline = adapt_project(project, 6, 'unknown', 30)
    assert timeline['schema'] == 'timeline/v1'
    assert timeline['preset'] == 'neutral'
    assert [clip['motion'] for clip in timeline['clips']] == ['static_focus', 'static_focus']
    assert validate_timeline(timeline) is timeline


def test_timeline_refuses_escape_and_manifest_is_atomic(tmp_path):
    timeline = {'schema': 'timeline/v1', 'fps': 30,
                'clips': [{'source': '../fora.png', 'start': 0, 'end': 1}], 'audio_events': []}
    with pytest.raises(ValueError):
        validate_timeline(timeline)
    good = {'schema': 'timeline/v1', 'fps': 30,
            'clips': [{'source': 'cenas/um.png', 'start': 0, 'end': 1}], 'audio_events': []}
    (tmp_path / 'cenas').mkdir(); (tmp_path / 'cenas/um.png').write_bytes(b'x')
    manifest = write_manifest(tmp_path, good)
    assert manifest.is_file() and not manifest.with_suffix('.tmp').exists()

@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_timeline_refuses_non_finite_times(value):
    timeline = {'schema': 'timeline/v1', 'fps': 30,
                'clips': [{'source': 'cenas/um.png', 'start': 0, 'end': value}], 'audio_events': []}
    with pytest.raises(ValueError): validate_timeline(timeline)


def test_invalidation_is_selective():
    project = Project(1, 'demo', 'Tema', 'Games', 'Short', 30,
                      statuses={'voz': 'CONCLUIDO', 'legendas': 'CONCLUIDO', 'video': 'CONCLUIDO'})
    assert invalidate_derived(project, {'preset'}) == {'video'}
    assert project.statuses == {'voz': 'CONCLUIDO', 'legendas': 'CONCLUIDO', 'video': 'PENDENTE'}


def test_explicit_scene_ranges_keep_narration_and_editorial_direction_outside_script():
    timeline = {'schema': 'timeline/v1', 'fps': 30, 'clips': [
        {'source': 'cenas/um.png', 'start': 0, 'end': 1.25,
         'narration_text': 'Gancho imediato.', 'editing_direction': 'Aproximação lenta.'},
        {'source': 'cenas/dois.png', 'start': 1.25, 'end': 3,
         'narration_text': 'Revelação.', 'editing_direction': 'Corte de impacto.'},
    ], 'audio_events': []}
    assert validate_timeline(timeline) is timeline
    assert scene_durations(timeline, 2, 10) == [1.25, 1.75]


def test_manual_timeline_is_proportionally_rescaled_to_real_narration_duration():
    project = Project(1, 'demo', 'Tema', 'Games', 'Short', 30, images=['cenas/um.png', 'cenas/dois.png'])
    project.settings['timeline'] = {
        'schema': 'timeline/v1', 'timing_mode': 'manual', 'fps': 30, 'preset': 'neutral',
        'clips': [
            {'source': 'cenas/um.png', 'start': 0, 'end': 2},
            {'source': 'cenas/dois.png', 'start': 2, 'end': 5},
        ], 'audio_events': [],
    }
    timeline = adapt_project(project, 10)
    assert [(clip['start'], clip['end']) for clip in timeline['clips']] == [(0.0, 4.0), (4.0, 10)]
    assert project.settings['timeline_duration_warning']['kind'] == 'narration_duration_rescaled'
    assert [clip['source'] for clip in timeline['clips']] == ['cenas/um.png', 'cenas/dois.png']


def test_retention_preset_accepts_six_to_twelve_images_and_serializes_safe_event_intents():
    with pytest.raises(ValueError, match='pelo menos 6'):
        validate_preset_images('Lore Oculta — Retenção Máxima', 5)
    validate_preset_images('Lore Oculta — Retenção Máxima', 8)
    with pytest.raises(ValueError, match='no máximo 12'):
        validate_preset_images('Lore Oculta — Retenção Máxima', 13)
    project = Project(1, 'demo', 'Tema', 'Games', 'Short', 30,
                      images=[f'cenas/{index}.png' for index in range(6)])
    timeline = adapt_project(project, 30, 'Lore Oculta — Retenção Máxima')
    assert timeline['preset'] == 'lore oculta — retenção máxima'
    assert len(timeline['clips']) == 6
    assert [event['intent'] for event in timeline['audio_events']] == ['impact', 'suspense', 'climax']
    assert validate_timeline(timeline) is timeline


def test_narrative_preset_uses_real_duration_and_deterministic_audio_intents():
    project = Project(1, 'demo', 'Tema', 'Games', 'Short', 30, images=['cenas/um.png', 'cenas/dois.png'])
    timeline = adapt_project(project, 10, 'História sombria')
    assert timeline['preset'] == 'história sombria'
    assert [event['intent'] for event in timeline['audio_events']] == ['suspense', 'impact']
    assert [event['time'] for event in timeline['audio_events']] == [0.0, 5.5]


def test_automatic_scene_cuts_anchor_to_real_word_and_sentence_boundaries():
    timeline = {
        'schema': 'timeline/v1', 'timing_mode': 'automatic', 'fps': 30, 'preset': 'neutral',
        'clips': [
            {'source': 'cenas/um.png', 'start': 0, 'end': .5},
            {'source': 'cenas/dois.png', 'start': .5, 'end': 1},
            {'source': 'cenas/tres.png', 'start': 1, 'end': 1.5},
        ], 'audio_events': [],
    }
    alignment = {'synchronized': True, 'words': [
        {'start': 0, 'end': .2, 'text': 'Primeira'},
        {'start': .2, 'end': .4, 'text': 'frase.'},
        {'start': .5, 'end': .7, 'text': 'Segunda'},
        {'start': .7, 'end': .9, 'text': 'frase.'},
        {'start': 1, 'end': 1.2, 'text': 'Terceira'},
        {'start': 1.2, 'end': 1.4, 'text': 'frase.'},
    ]}
    result = align_timeline_to_words(timeline, alignment, 'Primeira frase. Segunda frase. Terceira frase.', 1.5)
    assert [(clip['start'], clip['end']) for clip in result['clips']] == [(0.0, .5), (.5, 1.0), (1.0, 1.5)]
    assert [clip['narration_text'] for clip in result['clips']] == ['Primeira frase.', 'Segunda frase.', 'Terceira frase.']
    assert all(clip['timing_source'] == 'asr_word_alignment' for clip in result['clips'])
    assert validate_timeline(result) is result


def test_scene_alignment_requires_real_synchronized_asr_words():
    timeline = {'schema': 'timeline/v1', 'fps': 30, 'clips': [
        {'source': 'cenas/um.png', 'start': 0, 'end': 1},
        {'source': 'cenas/dois.png', 'start': 1, 'end': 2},
    ], 'audio_events': []}
    with pytest.raises(ValueError, match='timestamps por palavra'):
        align_timeline_to_words(timeline, {}, 'Teste.', 2)
