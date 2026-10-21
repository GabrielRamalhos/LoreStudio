import threading
from pathlib import Path

import pytest

from app.core.models import Project, StepStatus
from app.services.editorial_director import timeline_from_response
from app.services.pipeline import Pipeline


class Projects:
    def __init__(self):
        self.repo = self
    def save(self, project):
        return project
    def planning_outcomes(self, _limit):
        return []
    def artifact(self, project, name):
        return Path(__file__)
    def artifact_dir(self, project):
        return Path(__file__).parent


def test_automatic_short_uses_bundled_comfy_profile_without_manual_json(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro preservado.')
    pipeline = Pipeline(Projects())
    monkeypatch.setattr('app.services.pipeline.generate_pack', lambda *_: {'scenes': [{'index': 1, 'text': 'Roteiro preservado.', 'prompt': 'Cena'}]})
    called = []
    monkeypatch.setattr(pipeline, 'execute', lambda *args: called.append(args[1]))
    captured = []
    def generated(_, generated_project, _scenes, comfy, _cancel):
        captured.append(comfy.workflow)
        generated_project.images = ['cenas/gerada-01.png']
        generated_project.statuses['imagens'] = StepStatus.DONE
    monkeypatch.setattr('app.services.pipeline.generate_scenes', generated)
    pipeline.execute_short(project, {'ollama_model': 'local', 'ollama_url': 'http://127.0.0.1:1', 'comfyui_url': 'http://127.0.0.1:8188'}, threading.Event())
    assert called == ['voz', 'legendas', 'video']
    assert project.settings['storyboard']['scenes'][0]['text'] == 'Roteiro preservado.'
    assert 'CheckpointLoaderSimple' in captured[0]


def test_automatic_short_keeps_user_script_when_storyboard_is_created(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Texto original, sem reescrita.')
    pipeline = Pipeline(Projects())
    pack = {'scenes': [{'index': 1, 'text': project.script, 'prompt': 'Cena'}]}
    monkeypatch.setattr('app.services.pipeline.generate_pack', lambda *_: pack)
    def generated(_, generated_project, *__):
        generated_project.images = ['cenas/gerada-01.png']
        generated_project.statuses['imagens'] = StepStatus.DONE
    monkeypatch.setattr('app.services.pipeline.generate_scenes', generated)
    monkeypatch.setattr(pipeline, 'execute', lambda *_: type('Result', (), {'name': 'video_final.mp4'})())
    result = pipeline.execute_short(project, {'ollama_model': 'local', 'ollama_url': 'http://127.0.0.1:1', 'comfyui_workflow': '{}'}, threading.Event())
    assert result.name == 'video_final.mp4'
    assert project.script == 'Texto original, sem reescrita.'


def test_automatic_short_preserves_manual_timeline_for_imported_images(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.', images=['origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    project.settings['timeline'] = {'timing_mode': 'manual'}
    pipeline = Pipeline(Projects())
    monkeypatch.setattr('app.services.pipeline.OllamaProvider', lambda *_: pytest.fail('Ollama não deveria ser chamado'))
    monkeypatch.setattr('app.services.pipeline.ComfyUIProvider', lambda *_: pytest.fail('ComfyUI não deveria ser chamado'))
    steps = []
    monkeypatch.setattr(pipeline, 'execute', lambda _project, step, *_: steps.append(step) or type('Result', (), {'name': 'video_final.mp4'})())
    pipeline.execute_short(project, {}, threading.Event())
    assert steps == ['voz', 'legendas', 'video']


def test_retention_preset_stops_before_voice_when_image_count_is_below_minimum(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.',
                      images=[f'cenas/{number}.png' for number in range(5)])
    project.statuses['imagens'] = StepStatus.DONE
    pipeline = Pipeline(Projects())
    monkeypatch.setattr(pipeline, 'execute', lambda *_: pytest.fail('A voz não deve iniciar'))
    with pytest.raises(ValueError, match='pelo menos 6'):
        pipeline.execute_short(project, {'editing_preset': 'Lore Oculta — Retenção Máxima'}, threading.Event())


def test_automatic_short_creates_director_plan_after_voice(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.', images=['origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    pipeline = Pipeline(Projects())
    calls = []
    prompts = []

    def execute(candidate, step, *_):
        calls.append(step)
        if step == 'voz':
            candidate.audio_duration = 4.0
        return type('Result', (), {'name': f'{step}.wav'})()

    class Director:
        model = 'local-model'
        cancel = None
        def __init__(self, *_): pass
        def complete_structured(self, prompt, _schema):
            prompts.append(prompt)
            return {'clips': [{'source': 'origem.png', 'start': 0, 'end': 4,
                               'motion': 'slow_push', 'transition': 'cut'}]}

    monkeypatch.setattr(pipeline, 'execute', execute)
    monkeypatch.setattr('app.services.pipeline.OllamaProvider', Director)
    pipeline.execute_short(project, {}, threading.Event())
    assert calls == ['voz', 'legendas', 'video']
    assert project.settings['timeline']['director']['model'] == 'local-model'
    assert project.settings['timeline']['clips'][0]['source'] == 'origem.png'
    assert 'Narration duration seconds: 4.000000' in prompts[0]


def test_automatic_short_reexports_without_regenerating_valid_narration(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.', images=['origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    pipeline = Pipeline(Projects())
    steps, statuses = [], []

    monkeypatch.setattr(pipeline, '_can_reuse_narration', lambda *_: True)
    monkeypatch.setattr(pipeline, '_plan_editorial_automatically', lambda *_: False)
    monkeypatch.setattr(pipeline.projects, 'set_status', lambda *_args: statuses.append(_args[1:]), raising=False)
    monkeypatch.setattr(pipeline, 'execute', lambda _project, step, *_: steps.append(step) or type('Result', (), {'name': 'video_final.mp4'})())

    result = pipeline.execute_short(project, {}, threading.Event())
    assert result.name == 'video_final.mp4'
    assert steps == ['video']
    assert statuses[0][0] == 'voz'


def test_narration_reuse_hash_changes_when_voice_or_script_changes():
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Primeiro roteiro.')
    settings = {'tts_provider': 'kokoro', 'kokoro_voice': 'pm_alex', 'narration_speed': 1.0}
    original = Pipeline._narration_input_hash(project, settings)
    project.script = 'Outro roteiro.'
    assert Pipeline._narration_input_hash(project, settings) != original
    project.script = 'Primeiro roteiro.'
    settings['kokoro_voice'] = 'pm_santa'
    assert Pipeline._narration_input_hash(project, settings) != original


def test_automatic_short_allows_visual_director_to_assign_images_by_match(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Primeiro o herói. Depois a cidade.',
                      images=['cenas/cidade.png', 'cenas/heroi.png'])
    project.statuses['imagens'] = StepStatus.DONE
    pipeline = Pipeline(Projects())

    def execute(candidate, step, *_):
        if step == 'voz':
            candidate.audio_duration = 4.0
        return type('Result', (), {'name': f'{step}.wav'})()

    class LocalModels:
        cancel = None
        def __init__(self, model, *_): self.model = model
        def complete_structured(self, prompt, _schema):
            assert 'choose the sequence that best illustrates' in prompt
            return {'clips': [
                {'source': 'cenas/heroi.png', 'start': 0, 'end': 2, 'motion': 'slow_push', 'transition': 'cut'},
                {'source': 'cenas/cidade.png', 'start': 2, 'end': 4, 'motion': 'slow_pull', 'transition': 'fade'},
            ]}

    monkeypatch.setattr(pipeline, 'execute', execute)
    monkeypatch.setattr('app.services.pipeline.OllamaProvider', LocalModels)
    monkeypatch.setattr('app.services.pipeline.describe_imported_images', lambda *_: [
        {'source': 'cenas/cidade.png', 'description': 'Cidade destruída', 'subjects': ['cidade'], 'mood': 'sombrio'},
        {'source': 'cenas/heroi.png', 'description': 'Herói em close', 'subjects': ['herói'], 'mood': 'tenso'},
    ])
    pipeline.execute_short(project, {
        'visual_analysis_enabled': True, 'visual_ollama_model': 'vision-local', 'ollama_model': 'texto-local',
    }, threading.Event())
    assert 'timeline' in project.settings, project.history
    timeline = project.settings['timeline']
    assert [clip['source'] for clip in timeline['clips']] == ['cenas/heroi.png', 'cenas/cidade.png']
    assert timeline['director']['visual_ordering'] is True
    assert project.settings['visual_analysis']['status'] == 'completed'


def test_automatic_short_reuses_completed_visual_analysis_when_retrying_plan(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.', images=['cenas/origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    project.audio_duration = 4.0
    project.settings['visual_analysis'] = {
        'status': 'completed', 'model': 'vision-local',
        'notes': [{'source': 'cenas/origem.png', 'description': 'Pessoa em destaque', 'subjects': ['pessoa'], 'mood': 'tenso'}],
    }
    pipeline = Pipeline(Projects())

    class Director:
        cancel = None
        def __init__(self, model, *_): self.model = model
        def complete_structured(self, prompt, _schema):
            assert 'choose the sequence that best illustrates' in prompt
            return {'clips': [{'source': 'cenas/origem.png', 'start': 0, 'end': 4,
                               'motion': 'slow_push', 'transition': 'cut'}]}

    monkeypatch.setattr('app.services.pipeline.OllamaProvider', Director)
    monkeypatch.setattr('app.services.pipeline.describe_imported_images', lambda *_: pytest.fail('A análise visual concluída deve ser reutilizada'))
    assert pipeline._plan_editorial_automatically(project, {
        'visual_analysis_enabled': True, 'visual_ollama_model': 'vision-local', 'ollama_model': 'texto-local',
    }, threading.Event()) is True
    assert project.settings['timeline']['director']['visual_ordering'] is True


def test_automatic_short_records_the_actual_local_director_error(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.', images=['origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    project.audio_duration = 4.0
    pipeline = Pipeline(Projects())

    class OfflineDirector:
        cancel = None
        def __init__(self, *_): pass
        def complete_structured(self, *_): raise RuntimeError('servidor local ocupado')

    monkeypatch.setattr('app.services.pipeline.OllamaProvider', OfflineDirector)
    assert pipeline._plan_editorial_automatically(project, {'ollama_model': 'texto-local'}, threading.Event()) is False
    assert 'servidor local ocupado' in project.history[-1]['message']


def test_automatic_short_preserves_existing_director_plan(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.', images=['origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    project.settings['timeline'] = timeline_from_response(
        {'clips': [{'source': 'origem.png', 'start': 0, 'end': 4,
                    'motion': 'slow_push', 'transition': 'cut'}]},
        script=project.script, sources=project.images, duration=4, model='previous-local-model',
    )
    original = project.settings['timeline']
    pipeline = Pipeline(Projects())
    steps = []

    def execute(candidate, step, *_):
        steps.append(step)
        if step == 'voz':
            candidate.audio_duration = 4
        return type('Result', (), {'name': f'{step}.wav'})()

    monkeypatch.setattr(pipeline, 'execute', execute)
    monkeypatch.setattr('app.services.pipeline.OllamaProvider', lambda *_: pytest.fail('O plano existente deve ser preservado'))
    pipeline.execute_short(project, {}, threading.Event())
    assert steps == ['voz', 'legendas', 'video']
    assert project.settings['timeline'] is original
    assert project.settings['timeline']['director']['model'] == 'previous-local-model'
    assert 'preservada' in project.history[-1]['message']


def test_automatic_short_falls_back_when_director_is_unavailable(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.', images=['origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    pipeline = Pipeline(Projects())
    calls = []

    def execute(candidate, step, *_):
        calls.append(step)
        if step == 'voz': candidate.audio_duration = 4.0
        return type('Result', (), {'name': f'{step}.wav'})()

    class OfflineDirector:
        def __init__(self, *_): pass
        def complete_structured(self, *_): raise OSError('offline')

    monkeypatch.setattr(pipeline, 'execute', execute)
    monkeypatch.setattr('app.services.pipeline.OllamaProvider', OfflineDirector)
    pipeline.execute_short(project, {}, threading.Event())
    assert calls == ['voz', 'legendas', 'video']
    assert 'timeline' not in project.settings
    assert 'deterministica' in project.history[-1]['message']


def test_automatic_short_cancellation_during_planning_never_starts_video(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.', images=['origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    pipeline = Pipeline(Projects())
    calls, cancel = [], threading.Event()

    def execute(candidate, step, *_):
        calls.append(step)
        if step == 'voz': candidate.audio_duration = 4.0
        return type('Result', (), {'name': f'{step}.wav'})()

    class CancellingDirector:
        def __init__(self, *_): pass
        def complete_structured(self, *_):
            cancel.set()
            raise RuntimeError('cancelled')

    monkeypatch.setattr(pipeline, 'execute', execute)
    monkeypatch.setattr('app.services.pipeline.OllamaProvider', CancellingDirector)
    with pytest.raises(RuntimeError, match='cancelada'):
        pipeline.execute_short(project, {}, cancel)
    assert calls == ['voz']
    assert 'timeline' not in project.settings


def test_stale_automatic_director_response_is_not_persisted(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Antes.', images=['origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    pipeline = Pipeline(Projects())
    calls = []

    def execute(candidate, step, *_):
        calls.append(step)
        if step == 'voz':
            candidate.audio_duration = 4
        return type('Result', (), {'name': f'{step}.wav'})()

    class StaleDirector:
        model = 'local-model'
        cancel = None
        def __init__(self, *_): pass
        def complete_structured(self, *_):
            project.script = 'Depois.'
            return {'clips': [{'source': 'origem.png', 'start': 0, 'end': 4,
                               'motion': 'slow_push', 'transition': 'cut'}]}

    monkeypatch.setattr(pipeline, 'execute', execute)
    monkeypatch.setattr('app.services.pipeline.OllamaProvider', StaleDirector)
    pipeline.execute_short(project, {}, threading.Event())
    assert calls == ['voz', 'legendas', 'video']
    assert 'timeline' not in project.settings
    assert 'deterministica' in project.history[-1]['message']


def test_stale_automatic_director_images_are_not_persisted(monkeypatch):
    project = Project(None, 'teste', 'Tema', 'Outros', 'Short', 30, script='Roteiro.', images=['origem.png'])
    project.statuses['imagens'] = StepStatus.DONE
    pipeline = Pipeline(Projects())
    calls = []

    def execute(candidate, step, *_):
        calls.append(step)
        if step == 'voz':
            candidate.audio_duration = 4
        return type('Result', (), {'name': f'{step}.wav'})()

    class StaleDirector:
        model = 'local-model'
        def __init__(self, *_): pass
        def complete_structured(self, *_):
            project.images[:] = ['substituida.png']
            return {'clips': [{'source': 'origem.png', 'start': 0, 'end': 4,
                               'motion': 'slow_push', 'transition': 'cut'}]}

    monkeypatch.setattr(pipeline, 'execute', execute)
    monkeypatch.setattr('app.services.pipeline.OllamaProvider', StaleDirector)
    pipeline.execute_short(project, {}, threading.Event())
    assert calls == ['voz', 'legendas', 'video']
    assert 'timeline' not in project.settings
    assert 'deterministica' in project.history[-1]['message']
