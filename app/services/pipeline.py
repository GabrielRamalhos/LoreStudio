from app.core.models import StepStatus
from pathlib import Path
from app.providers.piper import PiperProvider
from app.providers.kokoro import KokoroProvider
from app.services.voice import VoiceService
from app.services.subtitles import write_word_srt, duration_wav, alignment_metadata, rebuild_word_captions_from_alignment, CaptionAlignmentError
from app.services.video import render
from app.services.technical_validation import write_report
from app.services.settings import resolved_mapping, resolve_comfyui_workflow
from app.services.narration import normalize_text, speech_text
from app.providers.llm import OllamaProvider
from app.providers.comfyui import ComfyUIProvider
from app.services.ai import generate_pack
from app.services.script import generate_script
from app.services.image_generation import generate_scenes
from app.services.outcomes import planning_context
from app.services.music import build_mix_plan
from app.services.timeline import adapt_project, align_timeline_to_words, validate_director_timeline, is_unreviewed_legacy_timeline, write_manifest
from app.services.editing_presets import is_retention_preset, resolve_preset, validate_preset_images
from app.services.editorial_director import (
    prompt as editorial_prompt,
    response_schema as editorial_response_schema,
    stable_input_hash,
    timeline_from_response,
)
from app.services.visual_director import VISION_DIRECTOR_VERSION, describe_imported_images, editorial_context
from app.services.editorial_analysis import pre_export_report, write_pre_export_report
from datetime import datetime, timezone
from math import isfinite
import hashlib
import json
from time import perf_counter

class Pipeline:
    def __init__(self, projects):
        self.projects = projects

    @staticmethod
    def _has_word_captions(root: Path) -> bool:
        """Return true only for a renderable, word-timed ASS artifact.

        Older projects can carry an SRT from the legacy renderer.  An ASS
        header alone is also not enough: rendering it would silently produce
        a video with no visible captions.
        """
        ass = root / 'legendas.ass'
        try:
            from app.services.ass import validate_ass_file
            return (ass.is_file() and bool(validate_ass_file(ass))
                    and bool(alignment_metadata(root / 'legendas.srt').get('synchronized')))
        except (OSError, ValueError):
            return False

    @staticmethod
    def _narration_input_hash(project, settings: dict) -> str:
        """Fingerprint every input that can change the generated WAV.

        This deliberately does not use file modification times: a valid audio
        is reusable after a project folder is moved, while any change to the
        text, selected provider/voice, pronunciation dictionary or speech
        controls creates a new fingerprint.
        """
        provider = settings.get('tts_provider', 'piper')
        values = {
            'version': 1,
            'script': project.script,
            'pronunciation_dictionary': settings.get('pronunciation_dictionary', {}),
            'provider': provider,
            'speed': settings.get('narration_speed', 1.0),
            'sentence_pause': settings.get('sentence_pause', 0.3),
            'piper_model': settings.get('piper_model', '') if provider == 'piper' else '',
            'kokoro_voice': settings.get('kokoro_voice', '') if provider == 'kokoro' else '',
            'kokoro_python': settings.get('kokoro_python', '') if provider == 'kokoro' else '',
        }
        encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
        return hashlib.sha256(encoded.encode('utf8')).hexdigest()

    def _can_reuse_narration(self, project, settings: dict) -> bool:
        """Reuse only a complete WAV/word-caption pair for identical inputs."""
        root = self.projects.artifact_dir(project)
        return (
            project.statuses.get('voz') == StepStatus.DONE
            and (root / 'narracao.wav').is_file()
            and project.settings.get('narration_input_hash') == self._narration_input_hash(project, settings)
            and self._has_word_captions(root)
        )

    def _generate_word_captions(self, project, root: Path, settings, style):
        """Generate the mandatory ASS/SRT pair for the current narration."""
        result = write_word_srt(
            root / 'narracao.wav', root / 'legendas.srt',
            settings.get('asr_model', 'small'), style['subtitle_style'],
        )
        if not self._has_word_captions(root):
            raise ValueError('O ASR não produziu legendas por palavra utilizáveis para este áudio.')
        project.settings['caption_timing'] = 'word'
        project.settings['caption_alignment'] = alignment_metadata(root / 'legendas.srt')
        project.statuses['legendas'] = StepStatus.DONE
        return result

    def execute(self, project, step, settings, cancel):
        # Projects may be copied or moved after their tool paths were saved.
        # Resolve them for every individual step, not only for the one-click flow.
        settings = resolved_mapping(settings)
        service = self.projects
        service.set_status(project, step, StepStatus.PROCESSING)
        started = perf_counter()
        try:
            root = service.artifact_dir(project)
            if cancel.is_set():
                raise RuntimeError('Operação cancelada pelo usuário.')
            if step == 'voz':
                narration = normalize_text(project.script, settings.get('pronunciation_dictionary', {}))
                project.settings['narration_text'] = narration
                tts_input = speech_text(narration)
                project.settings['narration_tts_text'] = tts_input
                provider_name = settings.get('tts_provider', 'piper')
                if provider_name == 'kokoro':
                    provider = KokoroProvider(settings.get('kokoro_python', ''), settings.get('kokoro_voice', 'pm_alex'))
                    project.settings['voice_used'] = {'provider': 'kokoro', 'voice': provider.voice, 'python': provider.python, 'speed': settings.get('narration_speed', 1.0)}
                elif provider_name == 'piper':
                    provider = PiperProvider(settings['piper_executable'], settings['piper_model'])
                    project.settings['voice_used'] = {'provider': 'piper', 'model': settings['piper_model'], 'executable': settings['piper_executable'], 'speed': settings.get('narration_speed', 1.0), 'sentence_pause': settings.get('sentence_pause', 0.3)}
                else:
                    raise ValueError('Provedor de narração inválido.')
                result = VoiceService(provider).generate(project, root, cancel, text=tts_input, speed=settings.get('narration_speed', 1.0), sentence_pause=settings.get('sentence_pause', 0.3))
                # New narration invalidates both caption artifacts.  Keeping a
                # prior ASS/SRT would show words from a different audio file.
                (root / 'legendas.srt').unlink(missing_ok=True)
                (root / 'legendas.ass').unlink(missing_ok=True)
                style = resolve_preset(settings.get('editing_preset') or project.settings.get('editing_preset'))
                self._generate_word_captions(project, root, settings, style)
                project.settings['narration_input_hash'] = self._narration_input_hash(project, settings)
                project.statuses['video'] = StepStatus.PENDING
            elif step == 'legendas':
                if project.statuses.get('voz') != StepStatus.DONE:
                    raise ValueError('Gere uma narração válida para este roteiro antes de legendar.')
                style = resolve_preset(settings.get('editing_preset') or project.settings.get('editing_preset'))
                result = self._generate_word_captions(project, root, settings, style)
                project.statuses['video'] = StepStatus.PENDING
            elif step == 'video':
                if project.format != 'Short':
                    raise ValueError('A Fase 1 exporta apenas o formato Short.')
                if any(project.statuses.get(key) != StepStatus.DONE for key in ('voz', 'imagens')):
                    raise ValueError('Conclua imagens e voz antes de exportar.')
                # The regular one-click flow creates captions earlier.  This
                # additional guard repairs projects made by the old SRT-only
                # renderer and keeps "Exportar MP4" truly automatic.
                if not self._has_word_captions(root):
                    style = resolve_preset(settings.get('editing_preset') or project.settings.get('editing_preset'))
                    try:
                        rebuild_word_captions_from_alignment(
                            root / 'legendas.srt', root / 'narracao.wav', style['subtitle_style'],
                        )
                    except CaptionAlignmentError:
                        self._generate_word_captions(project, root, settings, style)
                    if not self._has_word_captions(root):
                        raise ValueError('As legendas sincronizadas nÃ£o puderam ser recuperadas automaticamente.')
                    project.history.append({
                        'time': datetime.now(timezone.utc).isoformat(),
                        'step': 'legendas',
                        'status': StepStatus.DONE.value,
                        'message': 'Legendas por palavra geradas automaticamente antes da exportação.',
                    })
                selected_preset = settings.get('editing_preset') or project.settings.get('editing_preset')
                validate_preset_images(selected_preset, len(project.images))
                preset_style = resolve_preset(selected_preset)
                music = settings.get('video_music') or None
                if music:
                    music = service.artifact(project, music)
                music_plan = settings.get('video_music_plan')
                if music_plan:
                    current_duration = project.audio_duration
                    if not current_duration or current_duration <= 0:
                        current_duration = duration_wav(root / 'narracao.wav')
                    try:
                        planned_duration = sum(float(value) for value in music_plan.get('scene_durations', []))
                    except (AttributeError, TypeError, ValueError):
                        planned_duration = -1
                    if len(music_plan.get('scene_durations', [])) != len(project.images) or abs(planned_duration - current_duration) > .01:
                        music_plan = build_mix_plan(current_duration, len(project.images), music_plan.get('beat_times'))
                        project.settings['video_music_plan'] = music_plan
                duration = duration_wav(root / 'narracao.wav')
                timeline = adapt_project(project, duration, selected_preset, settings.get('video_fps', 30))
                # Automatic plans and local-director plans keep the user media
                # order, but their scene transitions must follow the words
                # actually spoken in this WAV. A manually edited timeline is
                # intentionally authoritative and is never overwritten here.
                if timeline.get('timing_mode') == 'automatic' or isinstance(timeline.get('director'), dict):
                    timeline = align_timeline_to_words(
                        timeline, alignment_metadata(root / 'legendas.srt'),
                        project.settings.get('narration_text') or project.script, duration,
                    )
                    if isinstance(timeline.get('director'), dict):
                        from app.services.editorial_director import plan_hash
                        timeline['director']['plan_hash'] = plan_hash(timeline)
                    project.settings['timeline'] = timeline
                # Timelines manuais são normalizadas proporcionalmente em
                # adapt_project; a divergência original fica registrada no
                # projeto, sem bloquear a publicação.
                timeline_duration = duration
                if abs(timeline_duration - duration) > 0.15:
                    raise ValueError(
                        f'Os tempos da timeline totalizam {timeline_duration:.2f}s, mas a narraÃ§Ã£o dura {duration:.2f}s. '
                        'Ajuste o fim da Ãºltima cena ou regenere a narraÃ§Ã£o antes de exportar.'
                    )
                project.settings['editing_preset'] = timeline['preset']
                has_motion = any(clip.get('motion', 'static_focus') != 'static_focus' for clip in timeline['clips'])
                has_transition = any(clip.get('transition', 'cut') != 'cut' for clip in timeline['clips'][1:])
                event_assets = []
                omitted_events = []
                licensed_effects = settings.get('licensed_effects') or project.settings.get('licensed_effects') or {}
                if not isinstance(licensed_effects, dict):
                    raise ValueError('Biblioteca de efeitos licenciados inválida. Selecione o catálogo novamente.')
                bound_events = []
                for event in timeline.get('audio_events', []):
                    bound = dict(event)
                    record = licensed_effects.get(bound.get('intent'))
                    if not bound.get('asset') and isinstance(record, dict):
                        asset, license_text = record.get('path'), record.get('license')
                        if isinstance(asset, str) and asset and isinstance(license_text, str) and license_text.strip():
                            bound['asset'] = asset
                            bound['license'] = license_text.strip()
                            bound['title'] = str(record.get('title', bound['intent'])).strip()
                    bound_events.append(bound)
                timeline['audio_events'] = bound_events
                if isinstance(timeline.get('director'), dict):
                    from app.services.editorial_director import plan_hash
                    timeline['director']['plan_hash'] = plan_hash(timeline)
                project.settings['timeline'] = timeline
                effect_gain = {'off': 0.0, 'moderada': .35, 'intensa': .50}.get(settings.get('effects_intensity', 'moderada'), .35)
                for event in timeline.get('audio_events', []):
                    asset = event.get('asset')
                    if not asset:
                        omitted_events.append(event.get('intent', 'unknown'))
                        continue
                    path = service.artifact(project, asset)
                    if path.is_file() and effect_gain > 0:
                        event_assets.append({'path': path, 'time': event.get('time', 0), 'gain': event.get('gain', effect_gain)})
                    else:
                        omitted_events.append(event.get('intent', 'unknown'))
                project.settings['audio_event_omissions'] = omitted_events
                # Informational only: preserve the user's current timeline and
                # allow export even when editorial observations need review.
                editorial_report = pre_export_report(
                    script=project.settings.get('narration_text') or project.script,
                    duration=duration,
                    alignment=alignment_metadata(root / 'legendas.srt'),
                    timeline=timeline,
                    narration_wav=root / 'narracao.wav',
                    ass_path=root / 'legendas.ass',
                    music_enabled=bool(music),
                    audio_event_omissions=omitted_events,
                )
                write_pre_export_report(editorial_report, root / 'pre_export_report.json')
                project.settings['pre_export_report'] = {
                    'version': editorial_report['version'],
                    'warning_count': len(editorial_report['warnings']),
                    'generated_for_duration': editorial_report['duration_seconds'],
                }
                result = render(settings['ffmpeg'], [service.artifact(project, name) for name in project.images], root / 'narracao.wav', root / 'legendas.srt', root / 'video_final.mp4', cancel,
                    motion=bool(settings.get('video_motion', False) or has_motion), transition=bool(settings.get('video_transition', False) or has_transition),
                    music=music, music_volume=settings.get('video_music_volume', 25) / 100,
                    music_plan=music_plan, fps=timeline['fps'],
                    ass=root / 'legendas.ass', timeline=timeline,
                    mix_profile=(settings.get('music_mix_profile') or preset_style.get('mix_profile') or 'equilibrada'),
                    event_assets=event_assets, require_word_captions=True)
                import json
                report_path = root / 'technical_report.json'
                report = json.loads(report_path.read_text(encoding='utf8')) if report_path.is_file() else {}
                if isinstance(report.get('audio_events'), dict):
                    report['audio_events']['omitted'] = omitted_events
                    write_report(report, report_path)
                sources = ([{'path': str(settings.get('video_music')), 'license': settings.get('video_music_license', '')}] if music else [])
                sources.extend(
                    {'path': event['asset'], 'license': event['license'], 'title': event.get('title', event['intent'])}
                    for event in timeline.get('audio_events', []) if event.get('asset') and event.get('license')
                )
                write_manifest(root, timeline, report=report, sources=sources)
                service.repo.save(project)
            else:
                raise ValueError('Etapa desconhecida.')
            project.settings.setdefault('step_durations_seconds', {})[step] = round(perf_counter() - started, 3)
            message = str(result.name)
            if step == 'voz':
                message += f' | voz: {project.settings["voice_used"].get("voice") or Path(project.settings["voice_used"]["model"]).name}'
            service.set_status(project, step, StepStatus.DONE, message)
            return result
        except Exception as error:
            service.set_status(project, step, StepStatus.ERROR, str(error))
            raise

    def _record_editorial_note(self, project, message: str):
        """Persist a non-blocking automatic-planning outcome for the project."""
        project.history.append({
            'time': datetime.now(timezone.utc).isoformat(),
            'step': 'planejamento_editorial',
            'status': StepStatus.DONE.value,
            'message': message,
        })
        self.projects.repo.save(project)

    def _plan_editorial_automatically(self, project, settings, cancel) -> bool:
        """Use the local director after voice generation without replacing manual work."""
        if cancel.is_set():
            raise RuntimeError('Operacao cancelada pelo usuario.')
        current = project.settings.get('timeline')
        if (isinstance(current, dict) and current.get('timing_mode') == 'manual'
                and not is_unreviewed_legacy_timeline(current)):
            self._record_editorial_note(project, 'Plano editorial automatico ignorado: a timeline manual foi preservada.')
            return False
        try:
            duration = float(project.audio_duration or 0)
            if duration <= 0:
                duration = duration_wav(self.projects.artifact(project, 'narracao.wav'))
            if not isfinite(duration) or duration <= 0:
                raise ValueError('a duracao da narracao nao e valida')
            sources = list(project.images)
            preset = settings.get('editing_preset') or project.settings.get('editing_preset')
            input_hash = stable_input_hash(project.script, sources, duration, preset)
            visual_context = ''
            visual_ordering = False
            if settings.get('visual_analysis_enabled'):
                model = str(settings.get('visual_ollama_model', '')).strip()
                if not model:
                    project.settings['visual_analysis'] = {
                        'status': 'unavailable', 'reason': 'Nenhum modelo visual local foi configurado.',
                    }
                else:
                    try:
                        # A previous successful analysis is part of the project
                        # state.  Reusing it avoids sending the same six images
                        # to the vision model every time the text director has a
                        # temporary failure.  It is safe only when it belongs to
                        # the selected model and exactly the current project media.
                        saved_visual = project.settings.get('visual_analysis')
                        notes = saved_visual.get('notes') if isinstance(saved_visual, dict) else None
                        reusable = (
                            isinstance(notes, list)
                            and saved_visual.get('status') == 'completed'
                            and saved_visual.get('model') == model
                            and [note.get('source') for note in notes if isinstance(note, dict)] == sources
                        )
                        if not reusable:
                            visual_provider = OllamaProvider(model, settings.get('ollama_url', ''))
                            visual_provider.cancel = cancel
                            notes = describe_imported_images(visual_provider, self.projects.artifact_dir(project), sources)
                        visual_context = editorial_context(notes)
                        visual_ordering = bool(visual_context)
                        project.settings['visual_analysis'] = {
                            'status': 'completed', 'version': VISION_DIRECTOR_VERSION,
                            'model': model, 'notes': notes,
                        }
                    except Exception as error:
                        if cancel.is_set():
                            raise RuntimeError('Operação cancelada pelo usuário.') from error
                        project.settings['visual_analysis'] = {
                            'status': 'unavailable', 'model': model, 'reason': str(error),
                        }
            if cancel.is_set():
                raise RuntimeError('cancelled')
            provider = OllamaProvider(settings.get('ollama_model', ''), settings.get('ollama_url', ''))
            provider.cancel = cancel
            response = provider.complete_structured(
                editorial_prompt(project.script, sources, duration, preset, visual_context, visual_ordering),
                editorial_response_schema(),
            )
            if cancel.is_set():
                raise RuntimeError('cancelled')
            # The second snapshot prevents stale AI output from overwriting a
            # newer script or media list in programmatic callers.
            current_duration = float(project.audio_duration or duration)
            if input_hash != stable_input_hash(project.script, list(project.images), current_duration, preset):
                raise ValueError('o projeto mudou durante o planejamento')
            timeline = timeline_from_response(
                response, script=project.script, sources=sources, duration=duration,
                model=getattr(provider, 'model', settings.get('ollama_model', '')),
                preset=preset, fps=settings.get('video_fps', 30), visual_ordering=visual_ordering,
            )
            validate_director_timeline(timeline, sources, duration)
            project.settings['timeline'] = timeline
            project.statuses['video'] = StepStatus.PENDING
            self._record_editorial_note(project, 'Plano editorial automatico criado pela IA local.')
            return True
        except RuntimeError as error:
            if cancel.is_set():
                raise RuntimeError('Operacao cancelada pelo usuario.')
            # Do not hide a local-model error behind a generic fallback.  The
            # user can then distinguish a temporary Ollama/model issue from a
            # completed visual analysis, and retry without reanalysing images.
            self._record_editorial_note(project, f'Plano editorial automatico indisponivel ({error}); a edicao deterministica sera usada.')
            return False
        except Exception as error:
            self._record_editorial_note(project, f'Plano editorial automatico indisponivel ({error}); a edicao deterministica sera usada.')
            return False

    def execute_short(self, project, settings, cancel):
        settings = resolved_mapping(settings)
        has_images = project.statuses.get('imagens') == StepStatus.DONE and bool(project.images)
        if has_images:
            if not all(self.projects.artifact(project, name).is_file() for name in project.images):
                raise ValueError('Uma imagem importada não está mais disponível. Importe-a novamente.')
        else:
            provider = OllamaProvider(settings.get('ollama_model', ''), settings.get('ollama_url', ''))
            provider.cancel = cancel
            if not project.script.strip():
                project.script = generate_script(provider, project.theme, project.duration, fiction=True, planning_context=planning_context(self.projects.repo))
                self.projects.repo.save(project)
            scenes = project.settings.get('storyboard', {}).get('scenes')
            if not scenes:
                pack = generate_pack(provider, project.script, min(30, max(1, project.duration // 12)), project.settings.get('visual_style', 'cinematografico'), planning_context(self.projects.repo))
                project.settings['storyboard'] = pack
                scenes = pack['scenes']
                self.projects.repo.save(project)
            comfy = ComfyUIProvider(settings.get('comfyui_url', ''), resolve_comfyui_workflow(settings), settings.get('comfyui_checkpoint', ''))
            generate_scenes(self.projects, project, scenes, comfy, cancel)
        if project.format != 'Short':
            raise ValueError('A geração automática está disponível apenas para Shorts.')
        if not project.script.strip():
            raise ValueError('Escreva ou gere um roteiro antes de criar o Short.')
        if not project.images:
            raise ValueError('Importe ao menos uma imagem antes de criar o Short.')
        if project.statuses.get('imagens') != StepStatus.DONE:
            raise ValueError('Conclua a importação das imagens antes de criar o Short.')
        validate_preset_images(settings.get('editing_preset') or project.settings.get('editing_preset'), len(project.images))
        # Voice establishes the real duration. The local director is optional:
        # on an unavailable or malformed Ollama response, rendering continues
        # through the existing deterministic timeline adapter.
        reuse_narration = self._can_reuse_narration(project, settings)
        if reuse_narration:
            # A second export with the same narration should not wait for TTS
            # and local ASR again.  The hash and validated ASS prevent stale
            # words from being silently reused after a script/voice change.
            self.projects.set_status(project, 'voz', StepStatus.DONE, 'Narração e legendas válidas reutilizadas.')
            result = self.projects.artifact(project, 'narracao.wav')
        else:
            result = self.execute(project, 'voz', settings, cancel)
        self._plan_editorial_automatically(project, settings, cancel)
        steps = ('video',) if reuse_narration else ('legendas', 'video')
        for step in steps:
            if cancel.is_set():
                raise RuntimeError('Operação cancelada pelo usuário.')
            result = self.execute(project, step, settings, cancel)
        return result
