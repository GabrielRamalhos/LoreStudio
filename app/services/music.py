"""Licensed local music catalog. Nothing is selected unless its license is declared."""
import json
import shutil
import subprocess
import uuid
from pathlib import Path
import numpy as np
from app.providers.process import executable, run_process

SUPPORTED_AUDIO = {'.wav', '.mp3', '.flac', '.ogg', '.m4a'}
EFFECT_INTENTS = {'suspense', 'revelation', 'impact', 'transition', 'emotional', 'climax'}

# All values are local FFmpeg parameters.  They are deliberately explicit in
# the manifest/report instead of promising a subjective volume relationship.
MIX_PROFILES = {
    'discreta': {'music_gain': .09, 'duck_ratio': 7, 'duck_threshold': .035, 'attack': 30, 'release': 450},
    'equilibrada': {'music_gain': .13, 'duck_ratio': 9, 'duck_threshold': .032, 'attack': 25, 'release': 360},
    'cinematografica': {'music_gain': .18, 'duck_ratio': 7, 'duck_threshold': .028, 'attack': 20, 'release': 300},
}

def resolve_mix_profile(value: str | None) -> tuple[str, dict]:
    key = str(value or '').strip().casefold()
    if key not in MIX_PROFILES:
        key = 'equilibrada'
    return key, dict(MIX_PROFILES[key])


def _uniform_scene_durations(duration: float, scenes: int) -> list[float]:
    return [duration / scenes] * scenes


def build_mix_plan(duration: float, scenes: int, beat_times: list[float] | None = None) -> dict:
    """Build a serializable, deterministic scene and mix plan.

    Beat points are deliberately optional: failure to detect them is never an
    export failure and falls back to equal scene durations.
    """
    if duration <= 0 or scenes <= 0:
        raise ValueError('A narra\u00e7\u00e3o e ao menos uma cena s\u00e3o necess\u00e1rias para planejar a trilha.')
    beats = []
    for point in beat_times or []:
        try:
            point = float(point)
        except (TypeError, ValueError):
            continue
        if np.isfinite(point) and 0 < point < duration:
            beats.append(round(point, 3))
    beats = sorted(set(beats))
    targets = [duration * index / scenes for index in range(1, scenes)]
    cuts, used = [], set()
    for target in targets:
        choices = [(abs(point - target), index, point) for index, point in enumerate(beats)
                   if index not in used and (not cuts or point - cuts[-1] >= .25) and duration - point >= .25]
        if not choices:
            return {'version': 1, 'scene_durations': _uniform_scene_durations(duration, scenes), 'beat_times': beats,
                    'cuts': targets, 'beat_detection': 'fallback_uniform',
                    'notice': 'Batidas insuficientes; cortes distribu\u00eddos uniformemente.'}
        _, index, point = min(choices)
        used.add(index)
        cuts.append(point)
    points = [0.0, *cuts, duration]
    return {'version': 1, 'scene_durations': [points[index + 1] - points[index] for index in range(scenes)], 'beat_times': beats,
            'cuts': cuts, 'beat_detection': 'detected', 'notice': 'Cortes sincronizados com batidas detectadas localmente.'}


def detect_beats(ffmpeg: str, source: str | Path, duration: float) -> list[float]:
    """Detect local energy onsets; silence boundaries are not treated as beats."""
    program = executable(ffmpeg)
    try:
        result = subprocess.run([program, '-hide_banner', '-nostdin', '-i', str(source), '-t', str(duration), '-map', '0:a:0',
                                 '-ac', '1', '-ar', '11025', '-f', 'f32le', 'pipe:1'], capture_output=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not result.stdout:
            return []
    except Exception:
        return []
    samples = np.frombuffer(result.stdout, dtype=np.float32)
    frame, hop, rate = 1024, 512, 11025
    if len(samples) < frame:
        return []
    windows = np.lib.stride_tricks.sliding_window_view(samples, frame)[::hop]
    energy = np.sqrt(np.mean(windows * windows, axis=1))
    onset = np.maximum(0, np.diff(energy, prepend=energy[0]))
    threshold = float(np.median(onset) + max(np.std(onset) * 1.5, 0.002))
    minimum_gap = max(1, int(.25 * rate / hop))
    peaks = []
    for index in range(1, len(onset) - 1):
        if onset[index] < threshold or onset[index] < onset[index - 1] or onset[index] < onset[index + 1]:
            continue
        if peaks and index - peaks[-1] < minimum_gap:
            if onset[index] > onset[peaks[-1]]:
                peaks[-1] = index
            continue
        peaks.append(index)
    return [round(index * hop / rate, 3) for index in peaks if 0 < index * hop / rate < duration]


def validate_audio(ffmpeg: str, source: str | Path) -> Path:
    source = Path(source).expanduser().resolve()
    if source.suffix.lower() not in SUPPORTED_AUDIO or not source.is_file():
        raise ValueError('Selecione um arquivo de \u00e1udio suportado e dispon\u00edvel.')
    program = executable(ffmpeg)
    try:
        run_process([program, '-hide_banner', '-nostdin', '-v', 'error', '-i', str(source), '-map', '0:a:0', '-f', 'null', '-'])
    except Exception as error:
        raise ValueError(f'FFmpeg n\u00e3o conseguiu abrir a trilha: {source.name}. {error}') from error
    return source


def copy_music(project_service, project, source: str | Path, ffmpeg: str) -> tuple[str, dict]:
    """Validate before copying, then atomically add a project-owned track and plan."""
    source = validate_audio(ffmpeg, source)
    narration = project_service.artifact(project, 'narracao.wav')
    if not narration.is_file():
        raise ValueError('Gere a narra\u00e7\u00e3o antes de adicionar uma trilha.')
    from app.services.subtitles import duration_wav
    duration = duration_wav(narration)
    # Validate all prerequisites before creating a project-owned copy.
    build_mix_plan(duration, len(project.images))
    target_dir = project_service.artifact_dir(project) / 'trilha'
    target_dir.mkdir(exist_ok=True)
    target = target_dir / f'{uuid.uuid4().hex[:12]}{source.suffix.lower()}'
    temporary = target.with_name(target.stem + '.part' + target.suffix)
    try:
        shutil.copy2(source, temporary)
        validate_audio(ffmpeg, temporary)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    plan = build_mix_plan(duration, len(project.images), detect_beats(ffmpeg, target, duration))
    return target.relative_to(project_service.artifact_dir(project)).as_posix(), plan


def load_catalog(path: str | Path) -> list[dict]:
    manifest = Path(path).expanduser().resolve()
    if not manifest.is_file():
        raise FileNotFoundError(f'Catálogo de trilhas não encontrado: {manifest}. Crie um manifesto JSON local.')
    try:
        records = json.loads(manifest.read_text(encoding='utf8'))
    except json.JSONDecodeError as error:
        raise ValueError(f'Catálogo de trilhas inválido: {error.msg}.') from error
    if not isinstance(records, list):
        raise ValueError('O catálogo de trilhas deve ser uma lista JSON.')
    catalog = []
    for number, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise ValueError(f'Entrada {number} do catálogo é inválida.')
        title, source, license_text = (record.get(key) for key in ('title', 'file', 'license'))
        if not all(isinstance(value, str) and value.strip() for value in (title, source, license_text)):
            raise ValueError(f'Entrada {number} exige title, file e license não vazios.')
        audio = (manifest.parent / source).resolve()
        if audio.suffix.lower() not in SUPPORTED_AUDIO or not audio.is_file() or not audio.is_relative_to(manifest.parent):
            raise ValueError(f'Trilha inválida na entrada {number}: {source}. O arquivo deve existir dentro da pasta do catálogo.')
        catalog.append({'title': title.strip(), 'path': str(audio), 'license': license_text.strip()})
    return catalog


def load_effect_catalog(path: str | Path) -> list[dict]:
    """Read a local, licensed sound-effect catalog without downloading media.

    Expected JSON: ``[{"intent": "impact", "title": "Impacto", "file":
    "impact.wav", "license": "CC0"}]``.  Each declared intent is unique,
    and every asset must remain under the catalog directory.
    """
    manifest = Path(path).expanduser().resolve()
    if not manifest.is_file():
        raise FileNotFoundError(f'Catálogo de efeitos não encontrado: {manifest}.')
    try:
        records = json.loads(manifest.read_text(encoding='utf8'))
    except json.JSONDecodeError as error:
        raise ValueError(f'Catálogo de efeitos inválido: {error.msg}.') from error
    if not isinstance(records, list) or not records:
        raise ValueError('O catálogo de efeitos deve ser uma lista JSON não vazia.')
    catalog, used = [], set()
    for number, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise ValueError(f'Entrada {number} do catálogo de efeitos é inválida.')
        intent, title, source, license_text = (record.get(key) for key in ('intent', 'title', 'file', 'license'))
        if not isinstance(intent, str) or intent.strip() not in EFFECT_INTENTS:
            raise ValueError(f'Entrada {number} exige intent válido: {", ".join(sorted(EFFECT_INTENTS))}.')
        if intent in used:
            raise ValueError(f'O efeito para "{intent}" foi informado mais de uma vez.')
        if not all(isinstance(value, str) and value.strip() for value in (title, source, license_text)):
            raise ValueError(f'Entrada {number} exige title, file e license não vazios.')
        audio = (manifest.parent / source).resolve()
        if audio.suffix.lower() not in SUPPORTED_AUDIO or not audio.is_file() or not audio.is_relative_to(manifest.parent):
            raise ValueError(f'Efeito inválido na entrada {number}: {source}. O arquivo deve existir dentro da pasta do catálogo.')
        used.add(intent)
        catalog.append({'intent': intent, 'title': title.strip(), 'path': str(audio), 'license': license_text.strip()})
    return catalog


def copy_effect_catalog(project_service, project, catalog: list[dict], ffmpeg: str) -> dict:
    """Atomically copy licensed local effects into a project-owned directory."""
    if not catalog:
        raise ValueError('Selecione ao menos um efeito licenciado.')
    root = project_service.artifact_dir(project)
    target_dir = root / 'efeitos'
    target_dir.mkdir(exist_ok=True)
    copied, result = [], {}
    try:
        for record in catalog:
            intent = record['intent']
            source = validate_audio(ffmpeg, record['path'])
            target = target_dir / f'{intent}-{uuid.uuid4().hex[:12]}{source.suffix.lower()}'
            temporary = target.with_name(target.stem + '.part' + target.suffix)
            try:
                shutil.copy2(source, temporary)
                validate_audio(ffmpeg, temporary)
                temporary.replace(target)
            except Exception:
                temporary.unlink(missing_ok=True)
                target.unlink(missing_ok=True)
                raise
            copied.append(target)
            result[intent] = {
                'path': target.relative_to(root).as_posix(),
                'title': record['title'],
                'license': record['license'],
            }
    except Exception:
        for target in copied:
            target.unlink(missing_ok=True)
        raise
    return result
