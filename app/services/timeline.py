"""Versioned, serializable render plan and legacy-project adapter."""
import hashlib
import json
import math
import re
from pathlib import Path
from app.services.editing_presets import is_retention_preset, resolve_preset, validate_preset_images

VERSION = 'timeline/v1'
MOTIONS = {'static_focus', 'slow_push', 'slow_pull', 'pan_left', 'pan_right', 'impact_zoom'}
TRANSITIONS = {'cut', 'fade', 'dissolve', 'wipe', 'impact_cut', 'flash_transition'}


def _word_text(value: object) -> str:
    """Normalize a token only for boundary planning, never for display."""
    return re.sub(r"[^\wÀ-ÿ]+", "", str(value or ""), flags=re.UNICODE).casefold()


def _word_alignment_values(alignment: dict, duration: float) -> list[dict]:
    """Read the already-validated real ASR timestamps from the sidecar."""
    words = alignment.get('words') if isinstance(alignment, dict) else None
    if not isinstance(alignment, dict) or not alignment.get('synchronized') or not isinstance(words, list) or not words:
        raise ValueError('Não há timestamps por palavra sincronizados para alinhar as cenas.')
    values, previous = [], -1.0
    for item in words:
        if not isinstance(item, dict):
            raise ValueError('O alinhamento por palavra salvo está inválido.')
        try:
            start, end = float(item['start']), float(item['end'])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('O alinhamento por palavra salvo está inválido.') from error
        text = str(item.get('text', '')).strip()
        if (not text or not math.isfinite(start) or not math.isfinite(end)
                or start < previous - .001 or end <= start or start < 0 or end > duration + .05):
            raise ValueError('O alinhamento por palavra salvo está inválido.')
        values.append({'start': start, 'end': end, 'text': text})
        previous = start
    return values


def _sentence_boundaries(script: str, words: list[dict]) -> set[int]:
    """Return likely sentence boundaries as indexes *after* an ASR word.

    ASR punctuation is preferred. The script supplies additional candidates
    when the recognizer emitted bare tokens, so a scene can still change at a
    narrative boundary instead of at an arbitrary proportional position.
    """
    total = len(words)
    boundaries = {index + 1 for index, word in enumerate(words[:-1])
                  if str(word['text']).rstrip().endswith(('.', '!', '?', '…'))}
    script_tokens = re.findall(r"[\wÀ-ÿ]+|[.!?…]", script, flags=re.UNICODE)
    spoken = [_word_text(token) for token in script_tokens if _word_text(token)]
    if not spoken:
        return boundaries
    count = 0
    for token in script_tokens:
        if _word_text(token):
            count += 1
        elif token in {'.', '!', '?', '…'} and count and count < len(spoken):
            # The ASR may have recognised a variant of a proper name. Map the
            # script sentence position proportionally, but only as a cut
            # candidate; final cuts are always actual ASR word boundaries.
            boundaries.add(max(1, min(total - 1, round(count / len(spoken) * total))))
    return {value for value in boundaries if 0 < value < total}


def _choose_boundaries(candidates: set[int], word_count: int, clip_count: int) -> list[int]:
    """Choose ordered cuts close to evenly paced narrative units."""
    selected, previous = [], 0
    for position in range(1, clip_count):
        remaining = clip_count - position
        lower, upper = previous + 1, word_count - remaining
        target = position * word_count / clip_count
        available = [value for value in candidates if lower <= value <= upper]
        cut = min(available, key=lambda value: (abs(value - target), value)) if available else round(target)
        cut = max(lower, min(upper, cut))
        selected.append(cut)
        previous = cut
    return selected


def align_timeline_to_words(timeline: dict, alignment: dict, script: str, duration: float) -> dict:
    """Anchor automatic/director image cuts to real spoken-word boundaries.

    It does not decide whether an image *semantically* depicts a character;
    source order remains immutable. It makes the temporal editing honest: a
    cut occurs between words, and each clip records the exact narration it
    covers. A user-authored manual timeline is intentionally left untouched.
    """
    timeline = validate_timeline(timeline)
    clips = timeline['clips']
    if len(clips) == 1:
        clips[0].update(start=0.0, end=round(float(duration), 6), timing_source='asr_word_alignment', word_range={'start': 0, 'end': 0})
        return timeline
    words = _word_alignment_values(alignment, float(duration))
    if len(words) < len(clips):
        raise ValueError('A narração possui poucas palavras para sincronizar todas as imagens.')
    boundaries = _choose_boundaries(_sentence_boundaries(script, words), len(words), len(clips))
    indexes = [0, *boundaries, len(words)]
    for index, clip in enumerate(clips):
        first, after = indexes[index], indexes[index + 1]
        start = 0.0 if index == 0 else words[first]['start']
        end = float(duration) if index == len(clips) - 1 else words[after]['start']
        if end <= start:
            raise ValueError('Não foi possível criar cortes de cena em limites de palavra válidos.')
        clip.update(
            start=round(start, 6), end=round(end, 6),
            narration_text=' '.join(word['text'] for word in words[first:after]),
            timing_source='asr_word_alignment',
            word_range={'start': first, 'end': after - 1},
        )
    clips[-1]['end'] = round(float(duration), 6)
    return validate_timeline(timeline)


def is_unreviewed_legacy_timeline(timeline: dict) -> bool:
    """Identify old equal-split plans that were never authored scene by scene.

    Older releases persisted generated timelines as ``manual`` even though no
    user had chosen a cut.  We can safely upgrade only that narrow shape:
    equal consecutive durations, no director provenance and no scene notes,
    word ranges or explicit user-authorship marker.  A modern edited timeline
    carries ``user_authored`` and remains authoritative.
    """
    if not isinstance(timeline, dict) or timeline.get('timing_mode') != 'manual' or timeline.get('director'):
        return False
    clips = timeline.get('clips')
    if not isinstance(clips, list) or len(clips) < 2 or timeline.get('user_authored') is True:
        return False
    if any(any(key in clip for key in ('narration_text', 'editing_direction', 'timing_source', 'word_range')) for clip in clips if isinstance(clip, dict)):
        return False
    try:
        durations = [float(clip['end']) - float(clip['start']) for clip in clips]
        contiguous = abs(float(clips[0]['start'])) < .001 and all(
            abs(float(current['start']) - float(previous['end'])) < .001
            for previous, current in zip(clips, clips[1:])
        )
    except (KeyError, TypeError, ValueError):
        return False
    return contiguous and all(value > 0 for value in durations) and max(durations) - min(durations) < .002

def _relative(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or not value:
        raise ValueError('A timeline aceita somente caminhos relativos ao projeto.')
    return path.as_posix()

def dependency_hash(path: str | Path) -> str | None:
    path = Path(path)
    if not path.is_file(): return None
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()

def validate_timeline(timeline: dict) -> dict:
    if not isinstance(timeline, dict) or timeline.get('schema') != VERSION: raise ValueError('Timeline deve usar schema timeline/v1.')
    fps = timeline.get('fps', 30)
    if not isinstance(fps, int) or not 1 <= fps <= 60: raise ValueError('FPS da timeline inválido.')
    clips = timeline.get('clips')
    if not isinstance(clips, list) or not clips: raise ValueError('Timeline exige ao menos um clipe.')
    previous = 0.0
    for clip in clips:
        if not isinstance(clip, dict): raise ValueError('Clipe inválido.')
        _relative(clip.get('source', ''))
        start, end = clip.get('start'), clip.get('end')
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end) or start < previous or end <= start):
            raise ValueError('Tempos da timeline inválidos.')
        if clip.get('motion', 'static_focus') not in MOTIONS:
            raise ValueError('Movimento de clipe invalido.')
        if clip.get('transition', 'cut') not in TRANSITIONS:
            raise ValueError('Transicao de clipe invalida.')
        if not isinstance(clip.get('allow_crop', False), bool):
            raise ValueError('allow_crop deve ser booleano.')
        for field in ('narration_text', 'editing_direction'):
            value = clip.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f'{field} deve ser texto não vazio quando informado.')
        if 'timing_source' in clip and clip['timing_source'] not in {'asr_word_alignment'}:
            raise ValueError('Origem do tempo do clipe inválida.')
        if 'word_range' in clip:
            word_range = clip['word_range']
            if (not isinstance(word_range, dict) or set(word_range) != {'start', 'end'}
                    or not all(isinstance(word_range[key], int) and word_range[key] >= 0 for key in word_range)
                    or word_range['end'] < word_range['start']):
                raise ValueError('Intervalo de palavras do clipe inválido.')
        previous = end
    for event in timeline.get('audio_events', []):
        if not isinstance(event, dict) or event.get('intent') not in {'suspense', 'revelation', 'impact', 'transition', 'emotional', 'climax'}:
            raise ValueError('Evento de audio invalido.')
        if event.get('asset'):
            _relative(event['asset'])
        for field in ('time', 'gain'):
            if field in event and (not isinstance(event[field], (int, float)) or not math.isfinite(event[field])):
                raise ValueError('Parâmetro de evento de áudio inválido.')
        if 'time' in event and float(event['time']) < 0:
            raise ValueError('Evento de áudio não pode ter tempo negativo.')
    return timeline

def validate_director_timeline(timeline: dict, sources: list[str], duration: float) -> dict:
    """Validate invariants required only for generated director plans."""
    validate_timeline(timeline)
    allowed = {'schema', 'timing_mode', 'fps', 'preset', 'clips', 'subtitles', 'audio_events', 'dependencies', 'director'}
    if set(timeline) - allowed or timeline.get('timing_mode') != 'manual':
        raise ValueError('Plano do diretor contém campos ou modo não permitidos.')
    clips = timeline['clips']
    director = timeline.get('director')
    visual_ordering = isinstance(director, dict) and director.get('visual_ordering') is True
    clip_sources = [clip.get('source') for clip in clips]
    if visual_ordering:
        if set(clip_sources) != set(sources):
            raise ValueError('Plano visual do diretor deve usar somente as imagens importadas, cada uma uma vez.')
    elif clip_sources != list(sources):
        raise ValueError('Plano do diretor deve usar cada imagem exatamente uma vez e na ordem importada.')
    if len(clips) != len(sources) or len(set(sources)) != len(sources):
        raise ValueError('Plano do diretor exige uma cena por imagem importada.')
    permitted_clip = {'source', 'start', 'end', 'motion', 'transition', 'allow_crop', 'narration_text', 'editing_direction', 'timing_source', 'word_range'}
    previous = 0.0
    for clip in clips:
        if set(clip) - permitted_clip: raise ValueError('Plano do diretor contém campos de clipe não permitidos.')
        if abs(float(clip['start']) - previous) > .000001:
            raise ValueError('Plano do diretor possui lacuna, sobreposição ou início diferente de zero.')
        previous = float(clip['end'])
    if abs(previous - float(duration)) > .000001:
        raise ValueError('Plano do diretor deve terminar exatamente na duração da narração.')
    director = timeline.get('director')
    required_director = {'version', 'model', 'input_hash', 'plan_hash'}
    allowed_director = required_director | {'visual_ordering'}
    if (not isinstance(director, dict) or not required_director <= set(director)
            or set(director) - allowed_director
            or ('visual_ordering' in director and not isinstance(director['visual_ordering'], bool))):
        raise ValueError('Plano do diretor não possui proveniência completa.')
    if not all(isinstance(director[key], str) and director[key].strip() for key in required_director):
        raise ValueError('Proveniência do diretor inválida.')
    from app.services.editorial_director import DIRECTOR_VERSION, plan_hash
    if director['version'] != DIRECTOR_VERSION or director['plan_hash'] != plan_hash(timeline):
        raise ValueError('Proveniência ou hash do plano do diretor inválido.')
    return timeline

def legacy_timeline(images: list[str], duration: float, preset: str | None = None, fps: int = 30) -> dict:
    if not images or duration <= 0: raise ValueError('Imagens e duração válidas são necessárias para criar a timeline.')
    validate_preset_images(preset, len(images))
    style = resolve_preset(preset); part = duration / len(images)
    selected_preset = style['name'].lower()
    clips = [{'source': _relative(source), 'start': round(i * part, 6), 'end': round((i + 1) * part, 6),
              'motion': style['motion'], 'transition': style['transition'] if i else 'cut',
              'allow_crop': False} for i, source in enumerate(images)]
    events = []
    if is_retention_preset(preset):
        # These are reproducible editorial intents. Rendering uses an event
        # only when it has a declared, project-local licensed asset.
        events = [
            {'intent': 'impact', 'time': 0.0},
            {'intent': 'suspense', 'time': round(duration * .35, 6)},
            {'intent': 'climax', 'time': round(duration * .78, 6)},
        ]
    elif style.get('audio_intents'):
        # Named narrative presets use deterministic points in the real WAV;
        # an event is rendered only when the user supplied a licensed asset.
        positions = (.0, .55, .82)
        events = [
            {'intent': intent, 'time': round(duration * positions[min(index, len(positions) - 1)], 6)}
            for index, intent in enumerate(style['audio_intents'])
        ]
    return {'schema': VERSION, 'timing_mode': 'automatic', 'fps': fps, 'preset': selected_preset, 'clips': clips,
            'subtitles': {'srt': 'legendas.srt', 'ass': 'legendas.ass'}, 'audio_events': events, 'dependencies': {}}

def adapt_project(project, duration: float, preset: str | None = None, fps: int = 30) -> dict:
    current = project.settings.get('timeline')
    if current:
        current = validate_timeline(current)
        if isinstance(current.get('director'), dict):
            # The director's immutable media contract is checked again at
            # render time, before any duration adaptation can hide stale media.
            validate_director_timeline(current, project.images, float(current['clips'][-1]['end']))
        if current.get('timing_mode', 'automatic') == 'automatic':
            timeline = legacy_timeline(project.images, duration, preset or current.get('preset') or project.settings.get('editing_preset'), fps)
            project.settings['timeline'] = timeline
            return timeline
        # Uma timeline manual representa a intenção editorial. Quando a voz é
        # regenerada, mantenha a ordem e a proporção das cenas, em vez de
        # impedir a exportação ou voltar à divisão automática.
        end = float(current['clips'][-1]['end']) if current.get('clips') else 0.0
        if end > 0 and abs(end - duration) > .000001:
            ratio = duration / end
            for clip in current['clips']:
                clip['start'] = round(float(clip['start']) * ratio, 6)
                clip['end'] = round(float(clip['end']) * ratio, 6)
            current['clips'][-1]['end'] = round(duration, 6)
            # Keep the persisted plan fingerprint truthful after the established
            # narration-duration adaptation, while retaining its provenance.
            if isinstance(current.get('director'), dict):
                from app.services.editorial_director import plan_hash
                current['director']['plan_hash'] = plan_hash(current)
            project.settings['timeline_duration_warning'] = {
                'kind': 'narration_duration_rescaled',
                'previous_duration': round(end, 6),
                'narration_duration': round(duration, 6),
                'message': ('A timeline manual foi reescalada proporcionalmente para acompanhar a duração da narração. '
                            'Revise os tempos antes da próxima exportação.'),
            }
        else:
            project.settings.pop('timeline_duration_warning', None)
        project.settings['timeline'] = current
        return current
    timeline = legacy_timeline(project.images, duration, preset or project.settings.get('editing_preset'), fps)
    project.settings['timeline'] = timeline
    return timeline

def scene_durations(timeline: dict | None, count: int, duration: float) -> list[float]:
    """Use explicit scene ranges when a complete timeline supplies them.

    Older projects omit this information and retain the historical equal split.
    Ranges are represented independently from the script, so editorial notes do
    not modify the user's saved narration.
    """
    clips = (timeline or {}).get('clips', [])
    if len(clips) == count and all(isinstance(clip, dict) and {'start', 'end'} <= clip.keys() for clip in clips):
        try:
            values = [float(clip['end']) - float(clip['start']) for clip in clips]
        except (TypeError, ValueError):
            values = []
        if values and all(value > 0 for value in values):
            return values
    return [duration / count] * count

def dependency_map(root: Path, timeline: dict) -> dict:
    paths = [clip['source'] for clip in timeline['clips']]
    paths += [value for value in timeline.get('subtitles', {}).values() if value]
    paths += [event['asset'] for event in timeline.get('audio_events', []) if event.get('asset')]
    return {item: dependency_hash(root / item) for item in paths}

def invalidate_derived(project, changed: set[str]) -> set[str]:
    """Mark only derivatives affected by an edit as pending."""
    affected = set()
    if changed & {'script', 'voice'}:
        affected.update({'voz', 'legendas', 'video'})
        project.audio_duration = None
    elif changed & {'captions'}:
        affected.update({'legendas', 'video'})
    elif changed & {'images', 'timeline', 'preset', 'music', 'volume'}:
        affected.add('video')
    for step in affected:
        if step in project.statuses:
            project.statuses[step] = 'PENDENTE'
    return affected

def write_manifest(root: Path, timeline: dict, *, report: dict | None = None, sources: list[dict] | None = None) -> Path:
    validate_timeline(timeline)
    data = {'schema': VERSION, 'timeline': timeline, 'dependencies': dependency_map(root, timeline), 'technical_report': report or {}, 'sources': sources or []}
    target = root / 'render_manifest.json'; temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8'); temporary.replace(target)
    return target
