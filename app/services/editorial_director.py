"""Local-only, filename-based editorial planning for imported media."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from app.services.editing_presets import is_retention_preset, resolve_preset, validate_preset_images
from app.services.timeline import MOTIONS, TRANSITIONS, VERSION

DIRECTOR_VERSION = 'editorial-director/v1'


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def stable_input_hash(script: str, sources: list[str], duration: float, preset: str | None = None) -> str:
    data = {'version': DIRECTOR_VERSION, 'script': script, 'sources': list(sources),
            'duration': round(float(duration), 6), 'preset': (preset or 'neutral').lower()}
    return hashlib.sha256(_canonical(data).encode('utf8')).hexdigest()


def plan_hash(timeline: dict) -> str:
    value = deepcopy(timeline)
    if isinstance(value.get('director'), dict): value['director'].pop('plan_hash', None)
    return hashlib.sha256(_canonical(value).encode('utf8')).hexdigest()


def response_schema() -> dict:
    clip = {'type': 'object', 'additionalProperties': False,
            'required': ['source', 'start', 'end', 'motion', 'transition'],
            'properties': {'source': {'type': 'string'}, 'start': {'type': 'number'}, 'end': {'type': 'number'},
                           'motion': {'type': 'string', 'enum': sorted(MOTIONS)},
                           'transition': {'type': 'string', 'enum': sorted(TRANSITIONS)},
                           'allow_crop': {'type': 'boolean'}, 'narration_text': {'type': 'string'},
                           'editing_direction': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['clips'],
            'properties': {'clips': {'type': 'array', 'items': clip}}}


def prompt(script: str, sources: list[str], duration: float, preset: str | None = None, visual_context: str = '',
           visual_ordering: bool = False) -> str:
    visual_note = (
        '\nLocal visual notes below were generated from the supplied images. They describe visible elements only; '
        'use them cautiously for editorial notes, and do not invent identities or facts.\n' + visual_context
        if visual_context else ''
    )
    ordering_rule = (
        'The visual notes are available, so choose the sequence that best illustrates the narration. '
        'Use every supplied source exactly once, but you may reorder the supplied sources only when this improves the visual-to-narration match. '
        if visual_ordering else
        'This is filename-only planning: do not claim to have viewed image contents. '
        'Use every supplied source exactly once and in exactly this order. Do not invent, omit, reorder, or alter paths. '
    )
    return ('You are a local editorial director. Return only JSON matching the supplied schema. '
            + ordering_rule +
            'Clip 1 starts at 0; clips are contiguous; the last end equals the narration duration. '
            f'Allowed motions: {sorted(MOTIONS)}. Allowed transitions: {sorted(TRANSITIONS)}. '
            'Use only editorial notes grounded in the supplied script; notes do not replace it.\n'
            f'Preset: {(preset or "neutral").lower()}\nNarration duration seconds: {float(duration):.6f}\n'
            f'Supplied project sources: {_canonical(sources)}\nScript:\n{script}{visual_note}')


def fallback_plan(sources: list[str], duration: float, preset: str | None = None, fps: int = 30) -> dict:
    if not sources or duration <= 0: raise ValueError('Imagens e duração válidas são necessárias para o plano alternativo.')
    validate_preset_images(preset, len(sources))
    style = resolve_preset(preset); part = float(duration) / len(sources)
    return {'schema': VERSION, 'timing_mode': 'automatic', 'fps': int(fps), 'preset': style['name'].lower(),
            'clips': [{'source': source, 'start': round(index * part, 6), 'end': round((index + 1) * part, 6),
                       'motion': style['motion'], 'transition': style['transition'] if index else 'cut', 'allow_crop': False}
                      for index, source in enumerate(sources)],
            'subtitles': {'srt': 'legendas.srt', 'ass': 'legendas.ass'}, 'audio_events': [], 'dependencies': {}}


def _director_clips_with_exact_ranges(clips: list[dict], sources: list[str], duration: float) -> list[dict]:
    """Keep the director's visual decisions, but own numeric timing locally.

    Local models frequently round a final timestamp (for example, ``30`` for
    a 30.75-second WAV).  Image ordering, motion and transitions are useful
    editorial decisions; timeline ranges must instead be exact and are later
    snapped to actual ASR word boundaries by the pipeline.
    """
    if len(clips) != len(sources):
        raise ValueError('O diretor local deve retornar exatamente um clipe por imagem.')
    allowed = {'source', 'start', 'end', 'motion', 'transition', 'allow_crop', 'narration_text', 'editing_direction'}
    part = float(duration) / len(sources)
    normalized = []
    for index, raw in enumerate(clips):
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError('O diretor local retornou um clipe inválido.')
        if not isinstance(raw.get('source'), str) or raw.get('motion') not in MOTIONS or raw.get('transition') not in TRANSITIONS:
            raise ValueError('O diretor local retornou uma decisão visual inválida.')
        # Require numeric model output, but deliberately do not trust its
        # rounded values as render timings.
        if not all(isinstance(raw.get(field), (int, float)) for field in ('start', 'end')):
            raise ValueError('O diretor local retornou tempos inválidos.')
        clip = {key: deepcopy(value) for key, value in raw.items() if key not in {'start', 'end'}}
        clip['start'] = round(index * part, 6)
        clip['end'] = round(float(duration) if index == len(clips) - 1 else (index + 1) * part, 6)
        normalized.append(clip)
    return normalized


def timeline_from_response(response: dict, *, script: str, sources: list[str], duration: float, model: str,
                           preset: str | None = None, fps: int = 30, visual_ordering: bool = False) -> dict:
    if not isinstance(response, dict) or set(response) != {'clips'}: raise ValueError('O diretor local retornou campos não permitidos.')
    if not isinstance(response.get('clips'), list): raise ValueError('O diretor local não retornou uma lista de clipes.')
    validate_preset_images(preset, len(sources))
    timeline = {'schema': VERSION, 'timing_mode': 'manual', 'fps': int(fps), 'preset': resolve_preset(preset)['name'].lower(),
                'clips': _director_clips_with_exact_ranges(response['clips'], sources, duration),
                'subtitles': {'srt': 'legendas.srt', 'ass': 'legendas.ass'},
                'audio_events': [], 'dependencies': {}, 'director': {'version': DIRECTOR_VERSION, 'model': str(model).strip(),
                'input_hash': stable_input_hash(script, sources, duration, preset)}}
    if visual_ordering:
        # Explicit provenance: a reordered list is allowed only after a local
        # vision model described the user-owned media for this plan.
        timeline['director']['visual_ordering'] = True
    timeline['director']['plan_hash'] = plan_hash(timeline)
    return timeline
