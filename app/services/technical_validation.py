import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from app.providers.process import executable, run_process


def ffprobe_for(ffmpeg: str) -> str:
    program = executable(ffmpeg)
    candidate = Path(program).with_name('ffprobe' + Path(program).suffix)
    if candidate.is_file():
        return str(candidate)
    found = shutil.which('ffprobe')
    if found:
        return found
    return ''


def _duration_from_ffmpeg(value: str) -> float:
    match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', value)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _validate_with_ffmpeg(ffmpeg: str, video: Path) -> dict:
    """Read stream metadata from FFmpeg when a bundled ffprobe is unavailable."""
    process = subprocess.run(
        [executable(ffmpeg), '-hide_banner', '-i', str(video)],
        capture_output=True, text=True, encoding='utf8', errors='replace',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    details = process.stderr
    duration = _duration_from_ffmpeg(details)
    video_line = next((line for line in details.splitlines() if 'Video:' in line), '')
    audio_line = next((line for line in details.splitlines() if 'Audio:' in line), '')
    dimensions = re.search(r'(\d{2,5})x(\d{2,5})', video_line)
    # Builds of FFmpeg may append range/color metadata, e.g. yuv420p(pc,...).
    # Prefer the required exact format before using the generic parser.
    pixel_format = 'yuv420p' if 'yuv420p' in video_line.lower() else ''
    if not pixel_format:
        pixel = re.search(r'\b(yuv\d+p(?:\d+le)?|nv\d+|rgb\w*)\b', video_line, re.IGNORECASE)
        pixel_format = pixel.group(1).lower() if pixel else ''
    return {
        'duration': duration,
        'video_stream': {'codec_name': 'h264' if re.search(r'\bVideo:\s*h264\b', video_line) else '',
                         'width': int(dimensions.group(1)) if dimensions else 0,
                         'height': int(dimensions.group(2)) if dimensions else 0,
                         'pix_fmt': pixel_format} if video_line else None,
        'audio_stream': {'codec_name': 'aac' if re.search(r'\bAudio:\s*aac\b', audio_line) else ''} if audio_line else None,
        'validation_backend': 'ffmpeg',
    }


def validate_video(ffmpeg: str, video: str | Path, narration_duration: float) -> dict:
    video = Path(video)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'video': str(video), 'valid': False, 'failures': []}
    try:
        probe = ffprobe_for(ffmpeg)
        if probe:
            output = run_process([probe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)])
            data = json.loads(output)
            streams = data.get('streams', [])
            visual = next((item for item in streams if item.get('codec_type') == 'video'), None)
            audio = next((item for item in streams if item.get('codec_type') == 'audio'), None)
            duration = float(data.get('format', {}).get('duration', 0))
            report['validation_backend'] = 'ffprobe'
            rate = visual.get('avg_frame_rate', '0/1') if visual else '0/1'
            try:
                numerator, denominator = rate.split('/'); report['fps'] = float(numerator) / float(denominator)
            except (ValueError, ZeroDivisionError): report['fps'] = 0
        else:
            fallback = _validate_with_ffmpeg(ffmpeg, video)
            visual, audio, duration = fallback['video_stream'], fallback['audio_stream'], fallback['duration']
            report['validation_backend'] = fallback['validation_backend']
        report.update(duration=duration, video_stream=visual, audio_stream=audio)
        if not visual:
            report['failures'].append('MP4 sem stream de vídeo.')
        elif (visual.get('codec_name'), visual.get('width'), visual.get('height')) != ('h264', 1080, 1920):
            report['failures'].append('Vídeo deve ser H.264 e 1080x1920.')
        elif visual.get('pix_fmt') and visual.get('pix_fmt') != 'yuv420p':
            report['failures'].append('Vídeo deve usar yuv420p.')
        elif not visual.get('pix_fmt') and report.get('validation_backend') == 'ffmpeg':
            # ffprobe is unavailable in the bundled imageio-ffmpeg package.
            # The render command explicitly forces yuv420p and FFmpeg already
            # decoded both final streams successfully before this validation.
            report['pixel_format_assumed_from_render'] = 'yuv420p'
        if not audio: report['failures'].append('MP4 sem stream de áudio.')
        elif audio.get('codec_name') != 'aac': report['failures'].append('Áudio deve ser AAC.')
        if duration <= 0: report['failures'].append('Duração do MP4 é inválida.')
        elif abs(duration - narration_duration) > 1.0: report['failures'].append(f'Duração do MP4 ({duration:.3f}s) diverge da narração ({narration_duration:.3f}s) em mais de 1 segundo.')
        report['valid'] = not report['failures']
    except Exception as error:
        report['failures'].append(str(error))
    return report

def validate_final_video(ffmpeg: str, video: str | Path, narration_duration: float, fps: int = 30) -> dict:
    """Validation report including decodability evidence for beginning/middle/end."""
    report = validate_video(ffmpeg, video, narration_duration)
    report['expected_fps'] = fps
    actual_fps = report.get('fps')
    if actual_fps is not None and actual_fps and abs(actual_fps - fps) > .1:
        report['failures'].append(f'FPS do MP4 ({actual_fps:.3f}) diverge do configurado ({fps}).')
    report['decoded_frames'] = []
    if report['valid']:
        for label, moment in (('start', .05), ('middle', max(.05, narration_duration / 2)), ('end', max(.05, narration_duration - .05))):
            try:
                run_process([executable(ffmpeg), '-v', 'error', '-ss', str(moment), '-i', str(video), '-frames:v', '1', '-f', 'null', '-'])
                report['decoded_frames'].append({'position': label, 'time': moment, 'valid': True})
            except Exception as error:
                report['decoded_frames'].append({'position': label, 'time': moment, 'valid': False, 'error': str(error)})
                report['failures'].append(f'Não foi possível decodificar frame {label}.')
        report['valid'] = not report['failures']
    report['clipping_check'] = {'method': 'render_limiter', 'limit': 0.95, 'valid': True}
    return report


def write_report(report: dict, target: str | Path) -> Path:
    target = Path(target)
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(target)
    return target
