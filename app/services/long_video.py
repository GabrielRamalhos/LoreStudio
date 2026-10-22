"""Local, reproducible preparation of several Shorts from one source video."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

from app.core.models import Project, StepStatus
from app.providers.process import executable, run_process
from app.services.subtitles import alignment_metadata, write_word_srt
from app.services.ass import write_ass
from app.services.technical_validation import validate_final_video, write_report


@dataclass(frozen=True)
class LongVideoSegment:
    start: float
    end: float
    script: str
    title: str
    score: int

    def as_dict(self) -> dict:
        return asdict(self)


def import_source_video(service, batch: Project, source: str | Path) -> Path:
    """Copy an owned source video into the batch project without mutation."""
    source = Path(source)
    if not source.is_file() or source.suffix.casefold() not in {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'}:
        raise ValueError('Selecione um vídeo local válido (MP4, MOV, MKV, WEBM, AVI ou M4V).')
    target_dir = service.artifact(batch, 'fonte')
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f'original{source.suffix.casefold()}'
    temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.part')
    try:
        shutil.copy2(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    batch.settings['long_video_source'] = target.relative_to(service.artifact_dir(batch)).as_posix()
    return target


def extract_source_audio(ffmpeg: str, source: str | Path, target: str | Path, cancel=None) -> Path:
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise FileNotFoundError('O vídeo de origem não está disponível localmente.')
    target.parent.mkdir(parents=True, exist_ok=True)
    candidate = target.with_name(f'.{target.stem}.{uuid.uuid4().hex}.part.wav')
    try:
        run_process([executable(ffmpeg), '-y', '-hide_banner', '-nostdin', '-i', str(source), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(candidate)], cancel, log=target.parent / 'extracao-audio.log')
        candidate.replace(target)
    finally:
        candidate.unlink(missing_ok=True)
    return target


def transcribe_source_audio(wav: str | Path, target_srt: str | Path, model: str) -> list[dict]:
    """Use the existing real-word ASR contract; no estimated timestamps."""
    target_srt = write_word_srt(Path(wav), Path(target_srt), model=model, caption_style='retention')
    record = alignment_metadata(target_srt)
    words = record.get('words') if isinstance(record, dict) else None
    if not record.get('synchronized') or not isinstance(words, list) or not words:
        raise ValueError('A transcrição do vídeo não retornou timestamps reais por palavra.')
    return words


def _clean_title(text: str) -> str:
    words = re.sub(r'[^\wÀ-ÿ ]+', ' ', text, flags=re.UNICODE).split()
    return ' '.join(words[:7]).strip().title() or 'Trecho do vídeo'


def deterministic_segments(words: list[dict], *, count: int = 5, minimum_seconds: float = 20, maximum_seconds: float = 58) -> list[LongVideoSegment]:
    """Fallback for unavailable Ollama, using only spoken sentence boundaries.

    This does not claim editorial intelligence. It keeps the pipeline fully
    local and produces non-overlapping, reviewable candidates instead of
    inventing timestamps or changing the original transcript.
    """
    if not 1 <= count <= 20:
        raise ValueError('A quantidade de Shorts deve estar entre 1 e 20.')
    valid = []
    for word in words:
        try:
            start, end, text = float(word['start']), float(word['end']), str(word['text']).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if text and end > start >= 0:
            valid.append({'start': start, 'end': end, 'text': text})
    if not valid:
        raise ValueError('Não há palavras com timestamps reais para criar Shorts.')
    candidates, start_index = [], 0
    for index, word in enumerate(valid):
        elapsed = word['end'] - valid[start_index]['start']
        terminal = word['text'].endswith(('.', '!', '?'))
        if elapsed >= minimum_seconds and (terminal or elapsed >= maximum_seconds):
            selected = valid[start_index:index + 1]
            text = ' '.join(item['text'] for item in selected)
            candidates.append(LongVideoSegment(round(selected[0]['start'], 3), round(selected[-1]['end'], 3), text, _clean_title(text), min(100, len(selected))))
            start_index = index + 1
    # Keep an ending only when it is a complete, useful spoken unit.
    if start_index < len(valid):
        selected = valid[start_index:]
        if selected[-1]['end'] - selected[0]['start'] >= minimum_seconds:
            text = ' '.join(item['text'] for item in selected)
            candidates.append(LongVideoSegment(round(selected[0]['start'], 3), round(selected[-1]['end'], 3), text, _clean_title(text), min(100, len(selected))))
    if not candidates:
        raise ValueError('O vídeo não possui um trecho falado com duração suficiente para um Short.')
    # Spread selections across the source rather than returning five adjacent clips.
    if len(candidates) <= count:
        return candidates
    indexes = [round(index * (len(candidates) - 1) / (count - 1)) for index in range(count)] if count > 1 else [0]
    return [candidates[index] for index in dict.fromkeys(indexes)]


def persist_child_projects(repo, parent: Project, segments: list[LongVideoSegment], *, category: str | None = None) -> list[Project]:
    """Create isolated Short projects; the source batch is never overwritten."""
    source = str(parent.settings.get('long_video_source', '')).strip()
    if not source:
        raise ValueError('O vídeo de origem ainda não foi importado.')
    created = []
    for number, segment in enumerate(segments, 1):
        duration = max(15, min(180, round(segment.end - segment.start)))
        child = Project(None, '', segment.title, category or parent.category or 'Outros', 'Short', duration, segment.script)
        child.settings.update({
            'source_short': {'parent_project_id': parent.id, 'source': source, 'start': segment.start, 'end': segment.end,
                             'score': segment.score, 'mode': 'automatic-local'},
            'editing_preset': 'retencao_maxima',
        })
        child.statuses = {'fonte': StepStatus.DONE.value, 'planejamento': StepStatus.DONE.value, 'video': StepStatus.PENDING.value}
        child.history.append({'step': 'planejamento', 'status': StepStatus.DONE.value,
                              'message': f'Short automático #{number} criado do vídeo de origem ({segment.start:.2f}s–{segment.end:.2f}s).'})
        created.append(repo.save(child))
    return created


def write_batch_manifest(service, batch: Project, segments: list[LongVideoSegment]) -> Path:
    target = service.artifact(batch, 'shorts-planejados.json')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'version': 1, 'segments': [item.as_dict() for item in segments]}, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(target)
    return target


def write_child_captions(service, child: Project) -> Path:
    """Create an ASS timeline relative to one source-video cut."""
    source = child.settings.get('source_short')
    if not isinstance(source, dict) or not isinstance(source.get('parent_project_id'), int):
        raise ValueError('Este projeto não foi criado a partir de um vídeo longo.')
    parent = service.repo.get(source['parent_project_id'])
    alignment_name = str(parent.settings.get('long_video_alignment', 'fonte/transcricao.srt'))
    record = alignment_metadata(service.artifact(parent, alignment_name))
    raw_words = record.get('words') if isinstance(record, dict) else None
    if not record.get('synchronized') or not isinstance(raw_words, list):
        raise ValueError('A transcrição sincronizada do vídeo de origem não está disponível.')
    try:
        start, end = float(source['start']), float(source['end'])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('Os limites do Short de origem são inválidos.') from error
    words = []
    for item in raw_words:
        if not isinstance(item, dict):
            continue
        try:
            word_start, word_end = float(item['start']), float(item['end'])
        except (KeyError, TypeError, ValueError):
            continue
        if word_start >= start and word_end <= end and word_end > word_start:
            words.append({'start': round(word_start - start, 3), 'end': round(word_end - start, 3), 'text': item.get('text', '')})
    if not words:
        raise ValueError('O trecho selecionado não contém palavras sincronizadas para legendar.')
    target = service.artifact(child, 'legendas.ass')
    write_ass(words, target, style='retention')
    child.settings['source_short_caption_words'] = len(words)
    return target


def source_short_command(ffmpeg: str, source: str | Path, captions: str | Path, output: str | Path, *, start: float, end: float, fps: int = 30) -> list[str]:
    """Build a vertical H.264/AAC export from one source-video interval."""
    if not 0 <= start < end:
        raise ValueError('Os limites do Short de origem são inválidos.')
    duration = end - start
    captions = Path(captions).resolve().as_posix().replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\\\'")
    graph = (
        '[0:v]split[fg][bg];'
        '[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:10[blur];'
        '[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fit];'
        f"[blur][fit]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},ass=filename='{captions}'[video]"
    )
    return [executable(ffmpeg), '-y', '-hide_banner', '-nostdin', '-ss', f'{start:.3f}', '-i', str(source),
            '-t', f'{duration:.3f}', '-filter_complex', graph, '-map', '[video]', '-map', '0:a:0?',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
            '-movflags', '+faststart', str(output)]


def render_child_short(service, child: Project, ffmpeg: str, cancel=None, *, fps: int = 30) -> Path:
    """Render transactionally: a failed retry never replaces a valid MP4."""
    source_info = child.settings.get('source_short')
    if not isinstance(source_info, dict) or not isinstance(source_info.get('parent_project_id'), int):
        raise ValueError('Este projeto não foi criado a partir de um vídeo longo.')
    parent = service.repo.get(source_info['parent_project_id'])
    source = service.artifact(parent, str(source_info.get('source', '')))
    if not source.is_file():
        raise FileNotFoundError('O vídeo de origem não está mais disponível. Reimporte o vídeo longo.')
    captions = write_child_captions(service, child)
    output = service.artifact(child, 'video_final.mp4')
    candidate = output.with_name(f'.{output.stem}.{uuid.uuid4().hex}.part.mp4')
    try:
        run_process(source_short_command(ffmpeg, source, captions, candidate, start=float(source_info['start']), end=float(source_info['end']), fps=fps), cancel, log=service.artifact(child, 'ffmpeg.log'))
        duration = float(source_info['end']) - float(source_info['start'])
        report = validate_final_video(ffmpeg, candidate, duration, fps)
        write_report(report, service.artifact(child, 'technical_report.json'))
        if not report.get('valid'):
            raise ValueError('Validação técnica do MP4 falhou: ' + '; '.join(report.get('failures', [])))
        candidate.replace(output)
    finally:
        candidate.unlink(missing_ok=True)
    child.statuses['legendas'] = StepStatus.DONE.value
    child.statuses['video'] = StepStatus.DONE.value
    child.history.append({'step': 'video', 'status': StepStatus.DONE.value, 'message': 'Short vertical criado diretamente do vídeo de origem.'})
    service.repo.save(child)
    return output
