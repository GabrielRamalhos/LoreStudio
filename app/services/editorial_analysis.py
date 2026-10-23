"""Explainable, local editorial review for a Short before rendering.

The functions in this module only inspect persisted project inputs.  They do
not rewrite a script, regenerate narration, or alter a timeline.  None of the
returned observations is a prediction of YouTube retention.
"""
from __future__ import annotations

import math
import json
import re
import wave
from pathlib import Path

import numpy as np


REVELATION_WORDS = {
    'mas', 'porém', 'porem', 'revela', 'revelou', 'revelação', 'descobre',
    'descobriu', 'segredo', 'verdade', 'afinal', 'surpresa', 'entanto',
}
CTA_PATTERN = re.compile(
    r'\b(curta|curte|like|inscreva(?:-se)?|inscrito|comente|comentários?|'
    r'compartilhe|siga|segue)\b', re.IGNORECASE,
)


def _warning(kind: str, timestamp: float | None, cause: str, suggestion: str, *, severity: str = 'info') -> dict:
    return {
        'id': kind,
        'severity': severity,
        'timestamp': round(timestamp, 3) if timestamp is not None else None,
        'cause': cause,
        'suggestion': suggestion,
        'blocking': False,
    }


def _words(alignment: dict) -> list[dict]:
    values = alignment.get('words') if isinstance(alignment, dict) else None
    if not alignment.get('synchronized') or not isinstance(values, list):
        return []
    result = []
    for value in values:
        if not isinstance(value, dict):
            continue
        try:
            start, end = float(value['start']), float(value['end'])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(value.get('text', '')).strip()
        if text and math.isfinite(start) and math.isfinite(end) and end > start >= 0:
            result.append({'start': start, 'end': end, 'text': text})
    return result


def first_revelation(alignment: dict) -> dict:
    """Locate a likely first reveal from spoken words, with honest uncertainty."""
    for word in _words(alignment):
        token = re.sub(r'[^\wÀ-ÿ]+', '', word['text'], flags=re.UNICODE).casefold()
        if token in REVELATION_WORDS:
            return {'detected': True, 'timestamp': round(word['start'], 3), 'trigger': word['text']}
    return {'detected': False, 'timestamp': None, 'trigger': None}


def detect_silences(wav: str | Path, *, minimum_seconds: float = .65) -> list[dict]:
    """Detect substantial low-energy regions in a PCM WAV without ASR guesses."""
    wav = Path(wav)
    try:
        with wave.open(str(wav), 'rb') as handle:
            if handle.getsampwidth() != 2 or handle.getcomptype() != 'NONE':
                return []
            rate, channels = handle.getframerate(), handle.getnchannels()
            raw = handle.readframes(handle.getnframes())
    except (OSError, wave.Error):
        return []
    if rate <= 0 or channels <= 0 or not raw:
        return []
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if channels > 1:
        samples = samples[:len(samples) // channels * channels].reshape(-1, channels).mean(axis=1)
    frame = max(1, int(rate * .05))
    usable = len(samples) // frame * frame
    if not usable:
        return []
    levels = np.sqrt(np.mean(np.square(samples[:usable].reshape(-1, frame)), axis=1)) / 32768.0
    # Relative threshold avoids treating a quietly narrated recording as one
    # long silence; the absolute floor still catches digital silence.
    reference = float(np.percentile(levels, 85)) if len(levels) else 0.0
    threshold = max(.003, reference * .07)
    silent = levels <= threshold
    runs, first = [], None
    for index, value in enumerate(silent):
        if value and first is None:
            first = index
        elif not value and first is not None:
            start, end = first * frame / rate, index * frame / rate
            if end - start >= minimum_seconds:
                runs.append({'start': round(start, 3), 'end': round(end, 3), 'duration': round(end - start, 3)})
            first = None
    if first is not None:
        start, end = first * frame / rate, len(levels) * frame / rate
        if end - start >= minimum_seconds:
            runs.append({'start': round(start, 3), 'end': round(end, 3), 'duration': round(end - start, 3)})
    return runs


def inspect_timeline(timeline: dict, duration: float) -> list[dict]:
    clips = timeline.get('clips', []) if isinstance(timeline, dict) else []
    warnings, static_start = [], None
    for index, clip in enumerate(clips, 1):
        try:
            start, end = float(clip['start']), float(clip['end'])
        except (KeyError, TypeError, ValueError):
            continue
        length = end - start
        if length > 7:
            warnings.append(_warning(
                'long_scene', start,
                f'Cena {index} permanece por {length:.1f}s.',
                'Considere dividir a cena em um limite de frase ou usar uma imagem complementar.',
                severity='warning',
            ))
        static = clip.get('motion', 'static_focus') == 'static_focus'
        if static and static_start is None:
            static_start = start
        if not static and static_start is not None:
            if start - static_start > 8:
                warnings.append(_warning(
                    'static_visual_interval', static_start,
                    f'Não há movimento visual declarado por {start - static_start:.1f}s.',
                    'Revise as cenas e aplique movimento suave somente onde reforçar a narrativa.',
                    severity='warning',
                ))
            static_start = None
    if static_start is not None and duration - static_start > 8:
        warnings.append(_warning(
            'static_visual_interval', static_start,
            f'Não há movimento visual declarado por {duration - static_start:.1f}s até o fim.',
            'Revise as cenas finais; uma mudança visual pode reforçar o encerramento.',
            severity='warning',
        ))
    return warnings


def inspect_cta(script: str, duration: float) -> dict:
    text = ' '.join(str(script or '').split())
    matches = list(CTA_PATTERN.finditer(text))
    if not matches:
        return {'present': False, 'position': None}
    last = matches[-1]
    ratio = last.start() / max(1, len(text))
    return {'present': True, 'position': round(duration * ratio, 3)}


def inspect_caption_safe_area(ass_path: str | Path | None) -> dict:
    if not ass_path or not Path(ass_path).is_file():
        return {'valid': False, 'reason': 'ASS ausente'}
    try:
        content = Path(ass_path).read_text(encoding='utf8')
        line = next(line for line in content.splitlines() if line.startswith('Style: Default,'))
        fields = line.split(',')
        margin_v = int(fields[-2])
    except (OSError, StopIteration, ValueError, IndexError):
        return {'valid': False, 'reason': 'Estilo ASS não pôde ser lido'}
    return {'valid': 160 <= margin_v <= 420, 'margin_v': margin_v}


def pre_export_report(*, script: str, duration: float, alignment: dict, timeline: dict,
                      narration_wav: str | Path | None = None, ass_path: str | Path | None = None,
                      music_enabled: bool = False, audio_event_omissions: list[str] | None = None) -> dict:
    """Create non-blocking, explainable editorial observations."""
    duration = max(0.0, float(duration))
    warnings = inspect_timeline(timeline, duration)
    revelation = first_revelation(alignment)
    if revelation['detected'] and revelation['timestamp'] > 3.5:
        warnings.append(_warning(
            'late_first_revelation', revelation['timestamp'],
            f"A primeira revelação provável ('{revelation['trigger']}') aparece após {revelation['timestamp']:.1f}s.",
            'Considere abrir com a consequência ou o conflito já presente no roteiro.',
            severity='warning',
        ))
    elif not revelation['detected']:
        warnings.append(_warning(
            'revelation_not_detected', None,
            'A análise local não identificou uma palavra-sinal de revelação.',
            'Revise manualmente os primeiros segundos; isto não indica desempenho previsto.',
        ))
    silences = detect_silences(narration_wav) if narration_wav else []
    if narration_wav:
        for silence in silences:
            warnings.append(_warning(
                'relevant_silence', silence['start'],
                f"Silêncio de aproximadamente {silence['duration']:.1f}s na narração.",
                'Confirme se a pausa é intencional ou reduza-a ao regenerar a voz.',
                severity='warning',
            ))
    cta = inspect_cta(script, duration)
    if not cta['present']:
        warnings.append(_warning(
            'cta_missing', None, 'Nenhuma chamada para ação foi identificada no roteiro.',
            'Se fizer sentido para o vídeo, inclua um CTA no encerramento.',
        ))
    elif cta['position'] < duration * .70:
        warnings.append(_warning(
            'cta_early', cta['position'], 'O CTA foi identificado antes do último terço do vídeo.',
            'Considere mantê-lo próximo do encerramento para não interromper a narrativa.',
        ))
    captions = inspect_caption_safe_area(ass_path)
    if not captions['valid']:
        warnings.append(_warning(
            'caption_safe_area', None, f"Legendas fora da área segura ou indisponíveis: {captions.get('reason', 'margem inválida')}.",
            'Gere novamente as legendas sincronizadas antes da exportação.', severity='warning',
        ))
    omissions = sorted(set(str(value) for value in (audio_event_omissions or []) if str(value)))
    if omissions:
        warnings.append(_warning(
            'audio_event_omitted', None, f'Efeitos previstos sem ativo licenciado: {", ".join(omissions)}.',
            'Importe um catálogo de efeitos licenciado ou mantenha os eventos desligados.',
        ))
    return {
        'version': 1,
        'kind': 'editorial_pre_export_report',
        'duration_seconds': round(duration, 3),
        'first_revelation': revelation,
        'silences': silences,
        'cta': cta,
        'captions': captions,
        'audio': {'music_enabled': bool(music_enabled), 'omitted_events': omissions},
        'warnings': warnings,
        'notice': 'Os avisos são editoriais e não preveem retenção, alcance ou desempenho no YouTube.',
    }


def write_pre_export_report(report: dict, target: str | Path) -> Path:
    """Persist a report atomically; it is informational and never blocks render."""
    target = Path(target)
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(target)
    return target
