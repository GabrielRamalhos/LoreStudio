import json
import math
import re, wave
from pathlib import Path
from types import SimpleNamespace

class ASRConfigurationError(RuntimeError):
    """The local Whisper runtime or its selected model cannot be used."""


class CaptionAlignmentError(ValueError):
    """The ASR recognized text but did not expose reliable word alignment."""


MINIMUM_ALIGNMENT_CONFIDENCE = .35
MAX_BOUNDARY_REPAIR_SECONDS = .08
MAX_REPAIRED_WORD_RATIO = .05
def duration_wav(path: Path) -> float:
    with wave.open(str(path), 'rb') as w:
        frames = w.getnframes()
        duration = frames / w.getframerate()
        if duration <= 0 or len(w.readframes(frames)) != frames * w.getnchannels() * w.getsampwidth():
            raise ValueError('WAV vazio ou truncado.')
        return duration
def srt_time(value: float) -> str:
    ms=round(value*1000); return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"
def write_srt(script: str, wav: Path, target: Path) -> Path:
    parts=[x.strip() for x in re.split(r'(?<=[.!?])\s+',script) if x.strip()]
    if not parts: raise ValueError('Roteiro vazio.')
    total=duration_wav(wav); weight=sum(len(x) for x in parts); start=0.; rows=[]
    for i,text in enumerate(parts,1):
        end=total if i==len(parts) else start+total*len(text)/weight
        rows += [str(i),f'{srt_time(start)} --> {srt_time(end)}',text,'']; start=end
    temporary = target.with_suffix('.tmp')
    temporary.write_text('\n'.join(rows),encoding='utf8')
    temporary.replace(target)
    return target

def _word_value(word) -> tuple[float, float, str]:
    start, end, text = word.start, word.end, word.word.strip()
    if start is None or end is None or end < start or not text:
        raise ValueError('O ASR local retornou timestamps por palavra inválidos.')
    return float(start), float(end), text


def _validate_word_intervals(words, duration: float):
    """Accept only real, usable word timestamps returned by the ASR backend.

    A duration derived from character counts or a neighbouring timestamp is
    not speech alignment.  Captions must therefore stop here when Whisper's
    alignment is absent or invalid; the caller can retry another local model
    or edit a valid timing track explicitly.
    """
    values = []
    previous_start = -1.0
    for word in words:
        start, end, text = _word_value(word)
        if not all(math.isfinite(value) for value in (start, end)) or start < 0 or end < 0 or start < previous_start - .001:
            raise ValueError('O ASR local retornou timestamps por palavra inválidos.')
        if end < start - .001 or start > duration + .001:
            raise ValueError('O ASR local retornou timestamps por palavra inválidos.')
        values.append(SimpleNamespace(
            start=start, end=end, word=text, probability=_confidence(word), timing_repaired=False,
        ))
        previous_start = start
    # A zero-width token is only repairable in a tiny, observed local window.
    # We never distribute a phrase by text length.  Faster-Whisper occasionally
    # quantizes a word at exactly the shared boundary of its neighbours; in
    # that isolated case the following *observed* word end gives us a bounded
    # window for the token.  The repair remains explicit in the sidecar.
    repaired = []
    for index, item in enumerate(values):
        if item.end > item.start + .000001:
            continue
        previous = values[index - 1] if index else None
        following = values[index + 1] if index + 1 < len(values) else None
        gap = item.start - previous.end if previous is not None else 0.0
        if gap > .001 and previous.end <= item.start + .001:
            item.start = max(previous.end, item.end - min(MAX_BOUNDARY_REPAIR_SECONDS, gap))
            item.end = max(item.end, item.start + .001)
        elif (following is not None and following.start <= item.start + .001
              and following.end > item.start + .001):
            # Word boundaries from Whisper use a coarse time grid.  Preserve
            # the returned start and use at most 80 ms from the immediately
            # following observed interval, never a duration inferred from text.
            item.end = min(following.end, item.start + MAX_BOUNDARY_REPAIR_SECONDS)
        else:
            continue
        item.timing_repaired = True
        repaired.append(index)
    maximum_repairs = max(2, math.floor(len(values) * MAX_REPAIRED_WORD_RATIO))
    if len(repaired) > maximum_repairs:
        raise CaptionAlignmentError('O ASR local retornou muitos timestamps por palavra sem duração utilizável.')
    if any(item.end <= item.start + .000001 for item in values):
        raise CaptionAlignmentError(
            'O ASR local reconheceu palavras, mas não retornou timestamps reais utilizáveis. '
            'As legendas não foram marcadas como sincronizadas. Tente regenerar a narração ou use outro modelo ASR local.'
        )
    return values


# Compatibility name retained for callers from earlier releases.  It now
# validates instead of fabricating durations.
_repair_zero_length_word_intervals = _validate_word_intervals


def alignment_path(target: Path) -> Path:
    return Path(target).with_suffix('.words.json')


def alignment_metadata(target: Path) -> dict:
    path = alignment_path(target)
    try:
        value = json.loads(path.read_text(encoding='utf8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _confidence(word) -> float | None:
    value = getattr(word, 'probability', None)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 <= value <= 1 else None


def _write_alignment(target: Path, words, duration: float, model: str) -> dict:
    confidences = [_confidence(word) for word in words]
    known = [value for value in confidences if value is not None]
    record = {
        'version': 1,
        'provider': 'faster-whisper',
        'model': str(model),
        'language': 'pt',
        'duration': duration,
        'synchronized': True,
        'confidence': {'available': bool(known), 'average': sum(known) / len(known) if known else None,
                       'minimum': min(known) if known else None, 'threshold': MINIMUM_ALIGNMENT_CONFIDENCE},
        'boundary_repairs': [index for index, word in enumerate(words) if getattr(word, 'timing_repaired', False)],
        'words': [
            {'index': index, 'start': word.start, 'end': word.end, 'text': word.word,
             'confidence': confidences[index], 'timing_repaired': bool(getattr(word, 'timing_repaired', False))}
            for index, word in enumerate(words)
        ],
    }
    path = alignment_path(target)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(path)
    return record


def _require_usable_confidence(words) -> None:
    values = [_confidence(word) for word in words]
    known = [value for value in values if value is not None]
    if known and sum(known) / len(known) < MINIMUM_ALIGNMENT_CONFIDENCE:
        raise CaptionAlignmentError(
            'O ASR local retornou baixa confiança média no alinhamento por palavra. '
            'As legendas não foram marcadas como sincronizadas; regenere a narração ou troque o modelo ASR local.'
        )


def rebuild_word_captions_from_alignment(target: Path, wav: Path, caption_style: str = 'standard') -> Path:
    """Rebuild SRT/ASS from real word timings saved for the current WAV.

    This repairs old invalid ASS syntax without re-running ASR. It never
    estimates timestamps and refuses a sidecar from another narration.
    """
    target, wav = Path(target), Path(wav)
    record = alignment_metadata(target)
    raw_words = record.get('words') if isinstance(record, dict) else None
    if not record.get('synchronized') or not isinstance(raw_words, list) or not raw_words:
        raise CaptionAlignmentError('NÃ£o hÃ¡ alinhamento por palavra salvo para recuperar.')
    try:
        saved_duration = float(record.get('duration'))
        current_duration = duration_wav(wav)
    except (TypeError, ValueError) as error:
        raise CaptionAlignmentError('O alinhamento salvo ou a narraÃ§Ã£o atual nÃ£o Ã© vÃ¡lida.') from error
    if abs(saved_duration - current_duration) > .05:
        raise CaptionAlignmentError('O alinhamento salvo pertence a outra narraÃ§Ã£o.')
    values = [SimpleNamespace(
        start=item.get('start'), end=item.get('end'), word=item.get('text'),
        probability=item.get('confidence'), timing_repaired=bool(item.get('timing_repaired', False)),
    ) for item in raw_words if isinstance(item, dict)]
    if len(values) != len(raw_words):
        raise CaptionAlignmentError('O alinhamento salvo estÃ¡ invÃ¡lido.')
    values = _validate_word_intervals(values, current_duration)
    _require_usable_confidence(values)
    from app.services.ass import write_ass
    ass_target = target.with_suffix('.ass')
    write_ass([{'start': word.start, 'end': word.end, 'text': word.word} for word in values], ass_target, style=caption_style)
    rows = []
    for number, (start, end, text) in enumerate(_subtitle_blocks(values), 1):
        rows += [str(number), f'{srt_time(start)} --> {srt_time(end)}', text, '']
    temporary = target.with_suffix('.tmp')
    temporary.write_text('\n'.join(rows), encoding='utf8')
    temporary.replace(target)
    return target


def apply_word_adjustments(target: Path, adjustments: list[dict], caption_style: str = 'standard') -> dict:
    """Persist explicit user timing edits and rebuild the SRT/ASS pair.

    The dialog supplies only changed start/end values.  All remaining words,
    including their ASR confidence, remain intact.  Invalid edits never
    replace a previous valid caption artifact.
    """
    target = Path(target)
    record = alignment_metadata(target)
    words = record.get('words') if isinstance(record, dict) else None
    duration = record.get('duration') if isinstance(record, dict) else None
    if not record.get('synchronized') or not isinstance(words, list) or not words:
        raise CaptionAlignmentError('Não há um alinhamento por palavra válido para ajustar. Gere as legendas novamente.')
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        raise CaptionAlignmentError('A duração do alinhamento salvo é inválida.') from None
    values = []
    for index, value in enumerate(words):
        if not isinstance(value, dict):
            raise CaptionAlignmentError('O alinhamento salvo está inválido.')
        values.append(SimpleNamespace(
            start=value.get('start'), end=value.get('end'), word=value.get('text'),
            probability=value.get('confidence'), index=index,
        ))
    for edit in adjustments:
        if not isinstance(edit, dict) or not isinstance(edit.get('index'), int):
            raise ValueError('Ajuste de legenda inválido.')
        index = edit['index']
        if not 0 <= index < len(values):
            raise ValueError('Ajuste de legenda fora da sequência.')
        for field in ('start', 'end'):
            if field in edit:
                values[index].__setattr__(field, edit[field])
    values = _validate_word_intervals(values, duration)
    from app.services.ass import write_ass
    ass_target = target.with_suffix('.ass')
    write_ass([{'start': word.start, 'end': word.end, 'text': word.word} for word in values], ass_target, style=caption_style)
    rows = []
    for number, (start, end, text) in enumerate(_subtitle_blocks(values), 1):
        rows += [str(number), f'{srt_time(start)} --> {srt_time(end)}', text, '']
    temporary = target.with_suffix('.tmp')
    temporary.write_text('\n'.join(rows), encoding='utf8')
    temporary.replace(target)
    record['manual_adjustments'] = True
    record['words'] = [
        {'index': index, 'start': word.start, 'end': word.end, 'text': word.word,
         'confidence': _confidence(word)}
        for index, word in enumerate(values)
    ]
    path = alignment_path(target)
    metadata_temporary = path.with_suffix('.tmp')
    metadata_temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
    metadata_temporary.replace(path)
    return record

def _subtitle_blocks(words, max_chars: int = 42, max_duration: float = 2.8):
    """Group word-aligned speech into readable, short subtitle cards."""
    blocks, current = [], []
    for word in words:
        start, end, text = _word_value(word)
        proposed = ' '.join([item[2] for item in current] + [text])
        if current and (len(proposed) > max_chars or end - current[0][0] > max_duration):
            blocks.append((current[0][0], current[-1][1], ' '.join(item[2] for item in current)))
            current = []
        current.append((start, end, text))
        if text.endswith(('.', '!', '?')):
            blocks.append((current[0][0], current[-1][1], ' '.join(item[2] for item in current)))
            current = []
    if current:
        blocks.append((current[0][0], current[-1][1], ' '.join(item[2] for item in current)))
    return blocks

def _decode_wav_for_asr(wav: Path):
    """Decode WAV without relying on an optional PyAV keyword.

    faster-whisper 1.2 requests ``metadata_errors`` from PyAV, but some
    Windows PyAV builds expose ``open`` without that keyword. Passing decoded
    samples to Whisper avoids changing the installed package and keeps ASR
    usable with both APIs.
    """
    try:
        import av
        import numpy as np
        from faster_whisper import audio as whisper_audio
    except ImportError as error:
        raise ASRConfigurationError('Dependências de decodificação do ASR local indisponíveis.') from error
    resampler = av.audio.resampler.AudioResampler(format='s16', layout='mono', rate=16000)
    try:
        try:
            container = av.open(str(wav), mode='r', metadata_errors='ignore')
        except TypeError:
            container = av.open(str(wav), mode='r')
        raw = bytearray()
        dtype = None
        with container:
            frames = whisper_audio._ignore_invalid_frames(container.decode(audio=0))
            frames = whisper_audio._group_frames(frames, 500000)
            for frame in whisper_audio._resample_frames(frames, resampler):
                data = frame.to_ndarray()
                dtype = data.dtype
                raw.extend(data.tobytes())
        if dtype is None or not raw:
            raise ValueError('O WAV não contém amostras de áudio legíveis.')
        return np.frombuffer(raw, dtype=dtype).astype(np.float32) / 32768.0
    finally:
        del resampler

def write_word_srt(wav: Path, target: Path, model: str = 'small', caption_style: str = 'standard') -> Path:
    """Generate captions from validated word timestamps only.

    The accessible SRT groups words into readable cards, while the ASS file
    keeps the per-word timing used by the renderer.  Both artifacts come from
    the same ASR result; there is no estimated sentence-timing fallback.
    """
    duration = duration_wav(wav)
    ass_target = target.with_suffix('.ass')
    # A request to regenerate captions belongs to this WAV.  Remove prior
    # artifacts now so a failed ASR run cannot leave stale words available for
    # an export of the newly generated narration.
    target.unlink(missing_ok=True)
    ass_target.unlink(missing_ok=True)
    alignment_path(target).unlink(missing_ok=True)
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise ASRConfigurationError('ASR local indisponível: instale faster-whisper e baixe/configure um modelo local.') from error
    try:
        recognizer = WhisperModel(model, device='auto', compute_type='int8')
    except Exception as error:
        raise ASRConfigurationError(
            f"Não foi possível preparar o modelo ASR local '{model}'. No primeiro uso, "
            'verifique a conexão de rede e o espaço livre em disco; depois tente novamente.'
        ) from error
    try:
        audio = _decode_wav_for_asr(wav)
        segments, _ = recognizer.transcribe(audio, language='pt', word_timestamps=True, vad_filter=True)
        words = [word for segment in segments for word in (segment.words or [])]
    except Exception as error:
        raise RuntimeError(f'Falha ao transcrever a narração com o ASR local: {error}') from error
    if not words:
        raise ValueError('O ASR local não retornou palavras com timestamps para esta narração.')
    words = _validate_word_intervals(words, duration)
    _require_usable_confidence(words)
    from app.services.ass import write_ass
    # Validate and persist the word-level source before exposing a new SRT.
    # A failed ASS write means the ASR result cannot safely drive captions.
    write_ass([{'start': word.start, 'end': word.end, 'text': word.word} for word in words], ass_target, style=caption_style)
    rows = []
    for number, (start, end, text) in enumerate(_subtitle_blocks(words), 1):
        rows += [str(number), f'{srt_time(start)} --> {srt_time(end)}', text, '']
    temporary = target.with_suffix('.tmp')
    temporary.write_text('\n'.join(rows), encoding='utf8')
    temporary.replace(target)
    _write_alignment(target, words, duration, model)
    return target
