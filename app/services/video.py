import shutil
import tempfile
import math
from pathlib import Path
from app.providers.process import executable, run_process
from app.services.subtitles import duration_wav
from app.services.technical_validation import validate_final_video, write_report
from app.services.timeline import scene_durations
from app.services.music import resolve_mix_profile
from app.services.editing_presets import is_retention_preset

def professional_compose_filter() -> str:
    """Filtro simples, compatível com ``-vf`` e com a API legada.

    A composição profissional usa :func:`_compose_chain`, pois um filtro com
    ``split`` precisa de labels e, portanto, só é válido em ``filter_complex``.
    """
    return ('scale=1080:1920:force_original_aspect_ratio=decrease,'
            'pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1')


def _compose_chain(input_label: str, output_label: str, *, fps: int, motion: str = 'static_focus') -> str:
    """Cria uma cena 9:16 sem deformar nem recortar o primeiro plano.

    O fundo é a própria imagem ampliada e desfocada; o primeiro plano inteiro
    fica centralizado por cima. Labels exclusivos evitam colisões quando várias
    cenas são compiladas no mesmo filtergraph.
    """
    key = output_label.replace('[', '').replace(']', '')
    foreground = f'fg_{key}'
    background = f'bg_{key}'
    blur = f'blur_{key}'
    movement = ''
    if motion in {'slow_push', 'impact_zoom'}:
        # Movimento discreto somente no fundo: nunca corta a mídia enviada.
        maximum = '1.10' if motion == 'impact_zoom' else '1.06'
        movement = f',zoompan=z=min({maximum}\\,1+on*0.00035):d=1:s=1080x1920:fps={fps}'
    elif motion == 'slow_pull':
        movement = f',zoompan=z=max(1.0\\,1.06-on*0.00035):d=1:s=1080x1920:fps={fps}'
    elif motion == 'pan_left':
        movement = f',zoompan=z=1.06:x=iw-iw/zoom-on*0.35:y=(ih-ih/zoom)/2:d=1:s=1080x1920:fps={fps}'
    elif motion == 'pan_right':
        movement = f',zoompan=z=1.06:x=on*0.35:y=(ih-ih/zoom)/2:d=1:s=1080x1920:fps={fps}'
    return (
        f'{input_label}split[{foreground}][{background}];'
        f'[{background}]scale=1080:1920:force_original_aspect_ratio=increase,'
        f'crop=1080:1920,boxblur=20:10{movement}[{blur}];'
        f'[{foreground}]scale=1080:1920:force_original_aspect_ratio=decrease[{foreground}_fit];'
        f'[{blur}][{foreground}_fit]overlay=(W-w)/2:(H-h)/2,setsar=1{output_label}'
    )


def _caption_filter(filename: str, ass: bool) -> str:
    escaped = filename.replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\\\'")
    return f"{'ass' if ass else 'subtitles'}=filename='{escaped}'"

def ffmpeg_command(ffmpeg: str, image, audio: Path, srt: Path, output: Path, duration=None):
    images = [Path(image)] if isinstance(image, (str, Path)) else list(image)
    escaped = srt.as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\\\'")
    # Preserve every edge of manually supplied media.  Filling 9:16 by cropping
    # would silently discard content from landscape or square source images.
    scale = professional_compose_filter()
    command = [ffmpeg, '-y', '-hide_banner', '-nostdin']
    for item in images:
        command += ['-loop', '1', '-framerate', '25', '-i', str(item)]
    command += ['-i', str(audio)]
    if len(images) == 1:
        command += ['-vf', f"{scale},subtitles=filename='{escaped}'"]
    else:
        segment = duration / len(images)
        filters = [f'[{i}:v]{scale},trim=duration={segment:.6f},setpts=PTS-STARTPTS[v{i}]' for i in range(len(images))]
        filters.append(''.join(f'[v{i}]' for i in range(len(images))) + f"concat=n={len(images)}:v=1:a=0,subtitles=filename='{escaped}'[v]")
        command += ['-filter_complex', ';'.join(filters), '-map', '[v]', '-map', f'{len(images)}:a:0']
    if duration is not None:
        command += ['-t', str(duration)]
    return command + ['-shortest', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(output)]

def styled_command(ffmpeg, images, audio, output, duration, motion, transition, music, music_volume, music_plan=None, fps=30, *, timeline=None, caption_file='legendas.srt', ass=False, mix_profile='equilibrada', event_assets=None):
    # Cada cena inclui sua sobreposição; a última termina junto com a voz.
    count = len(images)
    requested = (music_plan or {}).get('scene_durations', [])
    explicit = scene_durations(timeline, count, duration)
    has_explicit_ranges = bool(timeline and len(timeline.get('clips', [])) == count and
                               all(isinstance(clip, dict) and {'start', 'end'} <= clip.keys()
                                   for clip in timeline['clips']))
    scene_lengths = explicit if has_explicit_ranges else (requested if len(requested) == count and all(value > 0 for value in requested) else explicit)
    # Crossfades change each visual boundary. Preserve beat-aligned durations exactly.
    fade = min(0.4, duration / (2 * count)) if transition and count > 1 and not (requested or has_explicit_ranges) else 0
    segment = (duration + (count - 1) * fade) / count
    command = [ffmpeg, '-y', '-hide_banner', '-nostdin']
    for item in images:
        command += ['-loop', '1', '-framerate', str(fps), '-i', str(item)]
    command += ['-i', str(audio)]
    if music:
        command += ['-stream_loop', '-1', '-i', str(music)]
    event_assets = list(event_assets or [])
    for event in event_assets:
        command += ['-i', str(event['path'])]
    filters = []
    for index in range(count):
        scene_duration = segment if fade else scene_lengths[index]
        clip = (timeline or {}).get('clips', [{}] * count)[index] if index < len((timeline or {}).get('clips', [])) else {}
        clip_motion = clip.get('motion', 'slow_push' if motion else 'static_focus')
        filters.append(_compose_chain(f'[{index}:v]', f'[composed{index}]', fps=fps, motion=clip_motion))
        filters.append(f'[composed{index}]format=yuv420p,trim=duration={scene_duration:.9f},setpts=PTS-STARTPTS,fps={fps},settb=AVTB[v{index}]')
    current = 'v0'
    if fade:
        for index in range(1, count):
            target = f'x{index}'
            clip = (timeline or {}).get('clips', [{}] * count)[index] if index < len((timeline or {}).get('clips', [])) else {}
            transition_name = clip.get('transition', 'fade')
            xfade_transition = {
                'fade': 'fade',
                'dissolve': 'dissolve',
                'wipe': 'wipeleft',
                'flash_transition': 'fadefast',
            }.get(transition_name, 'fade')
            filters.append(f'[{current}][v{index}]xfade=transition={xfade_transition}:duration={fade:.9f}:offset={index * (segment - fade):.9f}[{target}]')
            current = target
    elif count > 1:
        filters.append(''.join(f'[v{i}]' for i in range(count)) + f'concat=n={count}:v=1:a=0[joined]')
        current = 'joined'
    filters.append(f"[{current}]{_caption_filter(caption_file, ass)}[video]")
    filters.append(f'[{count}:a:0]asetpts=PTS-STARTPTS[voice]')
    audio_map = '[voice]'
    if music:
        profile_name, profile = resolve_mix_profile(mix_profile)
        effective_gain = min(1.0, float(music_volume) * profile['music_gain'] / .13)
        fade_time = min(1.0, duration / 2)
        filters.append(f'[{count+1}:a:0]atrim=duration={duration:.9f},asetpts=PTS-STARTPTS,loudnorm=I=-24:LRA=7:TP=-2,volume={effective_gain:.6f},afade=t=in:d={fade_time:.9f},afade=t=out:st={duration-fade_time:.9f}:d={fade_time:.9f}[music]')
        filters.append(f"[music][{count}:a:0]sidechaincompress=threshold={profile['duck_threshold']:.6f}:ratio={profile['duck_ratio']}:attack={profile['attack']}:release={profile['release']}[ducked]")
        filters.append(f'[voice][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0:latency=1[baseaudio]')
        audio_map = '[baseaudio]'
    event_start = count + 1 + (1 if music else 0)
    for index, event in enumerate(event_assets):
        delay = max(0, round(float(event.get('time', 0)) * 1000))
        gain = min(1.0, max(0.0, float(event.get('gain', .35))))
        source = f'[sfx{index}]'
        target = f'[mixed{index}]'
        filters.append(f'[{event_start + index}:a:0]asetpts=PTS-STARTPTS,adelay={delay}|{delay},volume={gain:.6f}{source}')
        filters.append(f'{audio_map}{source}amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0:latency=1{target}')
        audio_map = target
    command += ['-filter_complex', ';'.join(filters), '-map', '[video]', '-map', audio_map]
    return command + ['-t', str(duration), '-shortest', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(output)]

def render_sequential(ffmpeg, images, audio, srt, work, candidate, duration, cancel, log, fps=30, *, ass=False, timeline=None):
    """Evita o pico de RAM do filter_complex com todas as cenas abertas ao mesmo tempo."""
    segments = scene_durations(timeline, len(images), duration)
    scenes = []
    for index, image in enumerate(images):
        scene = work / f'scene-{index:03d}.mp4'
        clip = (timeline or {}).get('clips', [{}] * len(images))[index] if index < len((timeline or {}).get('clips', [])) else {}
        graph = _compose_chain('[0:v]', '[video]', fps=fps, motion=clip.get('motion', 'static_focus'))
        command = [ffmpeg, '-y', '-hide_banner', '-nostdin', '-loop', '1', '-framerate', str(fps), '-i', str(image),
                   '-t', f'{segments[index]:.6f}', '-filter_complex', graph, '-map', '[video]', '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                   '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(scene)]
        run_process(command, cancel, log=work / f'scene-{index:03d}.log')
        scenes.append(scene)
    manifest = work / 'scenes.txt'
    manifest.write_text(''.join(f"file '{scene.as_posix().replace("'", "'\\''")}'\n" for scene in scenes), encoding='utf8')
    joined = work / 'joined.mp4'
    run_process([ffmpeg, '-y', '-hide_banner', '-nostdin', '-f', 'concat', '-safe', '0', '-i', str(manifest), '-c', 'copy', str(joined)], cancel, log=work / 'concat.log')
    command = [ffmpeg, '-y', '-hide_banner', '-nostdin', '-i', str(joined), '-i', str(audio), '-vf', _caption_filter(srt.as_posix(), ass),
               '-t', str(duration), '-shortest', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-movflags', '+faststart', str(candidate)]
    run_process(command, cancel, log=log, cwd=work)


def render_audio_preview(ffmpeg, narration, output, *, music=None, music_volume=.25, mix_profile='equilibrada', cancel=None):
    """Render the same local voice/music treatment to a playable WAV preview."""
    program = executable(ffmpeg)
    narration, output = Path(narration), Path(output)
    if not narration.is_file():
        raise FileNotFoundError('Narração ausente para a prévia de áudio.')
    if not math.isfinite(music_volume) or not 0 <= music_volume <= 1:
        raise ValueError('Volume da trilha deve estar entre 0 e 1.')
    duration = duration_wav(narration)
    track = Path(music).resolve() if music else None
    if track and not track.is_file():
        raise FileNotFoundError('Trilha sonora ausente para a prévia de áudio.')
    output.parent.mkdir(parents=True, exist_ok=True)
    candidate = output.with_name(output.stem + '.tmp' + output.suffix)
    candidate.unlink(missing_ok=True)
    if track:
        _name, profile = resolve_mix_profile(mix_profile)
        gain = min(1.0, float(music_volume) * profile['music_gain'] / .13)
        fade = min(1.0, duration / 2)
        graph = (
            f'[1:a:0]atrim=duration={duration:.9f},asetpts=PTS-STARTPTS,'
            f'loudnorm=I=-24:LRA=7:TP=-2,volume={gain:.6f},'
            f'afade=t=in:d={fade:.9f},afade=t=out:st={duration-fade:.9f}:d={fade:.9f}[music];'
            f'[0:a:0]asetpts=PTS-STARTPTS[voice];'
            f'[music][0:a:0]sidechaincompress=threshold={profile["duck_threshold"]:.6f}:ratio={profile["duck_ratio"]}:'
            f'attack={profile["attack"]}:release={profile["release"]}[ducked];'
            '[voice][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0:latency=1[mix]'
        )
        command = [program, '-y', '-hide_banner', '-nostdin', '-i', str(narration), '-stream_loop', '-1', '-i', str(track),
                   '-filter_complex', graph, '-map', '[mix]', '-t', str(duration), '-c:a', 'pcm_s16le', str(candidate)]
    else:
        command = [program, '-y', '-hide_banner', '-nostdin', '-i', str(narration), '-t', str(duration),
                   '-c:a', 'pcm_s16le', str(candidate)]
    try:
        run_process(command, cancel)
        duration_wav(candidate)
        candidate.replace(output)
    finally:
        candidate.unlink(missing_ok=True)
    return output

def render(ffmpeg, image, audio, srt, output, cancel=None, *, motion=False, transition=False, music=None, music_volume=0.15, music_plan=None, fps=30, ass=None, timeline=None, mix_profile='equilibrada', event_assets=None, require_word_captions=False):
    program = executable(ffmpeg)
    images = [Path(image)] if isinstance(image, (str, Path)) else [Path(p) for p in image]
    audio, srt, output = Path(audio), Path(srt), Path(output)
    if not images or not all(path.is_file() for path in [*images, audio, srt]):
        raise FileNotFoundError('Imagem, áudio ou SRT ausente.')
    if not srt.read_text(encoding='utf8').strip():
        raise ValueError('SRT vazio.')
    duration = duration_wav(audio)
    if not math.isfinite(music_volume) or not 0 <= music_volume <= 1:
        raise ValueError('Volume da trilha deve estar entre 0 e 1.')
    if music:
        music = Path(music).resolve()
        if not music.is_file():
            raise FileNotFoundError('Trilha sonora ausente. Selecione novamente o arquivo local.')
    event_assets = [dict(event, path=Path(event['path']).resolve()) for event in (event_assets or [])]
    if not all(event['path'].is_file() for event in event_assets):
        raise FileNotFoundError('Efeito sonoro licenciado ausente. Remova ou selecione o ativo novamente.')
    output.parent.mkdir(parents=True, exist_ok=True)
    # A simple relative SRT name avoids FFmpeg filter quoting pitfalls on Windows.
    if not isinstance(fps, int) or not 1 <= fps <= 60:
        raise ValueError('FPS deve estar entre 1 e 60.')
    with tempfile.TemporaryDirectory(prefix='render-', dir=output.parent) as temporary:
        work = Path(temporary).resolve()
        shutil.copy2(srt, work / 'legendas.srt')
        use_ass = bool(ass and Path(ass).is_file())
        if require_word_captions and not use_ass:
            raise ValueError('A exportação exige legendas sincronizadas por palavra. Gere a narração novamente.')
        if use_ass:
            from app.services.ass import validate_ass_file
            ass_events = validate_ass_file(Path(ass))
            shutil.copy2(ass, work / 'legendas.ass')
        candidate = work / 'video.mp4'
        if motion or transition or music or event_assets:
            command = styled_command(program, [p.resolve() for p in images], audio.resolve(), candidate, duration, motion, transition, music, music_volume, music_plan, fps,
                                     timeline=timeline, caption_file='legendas.ass' if use_ass else 'legendas.srt', ass=use_ass, mix_profile=mix_profile, event_assets=event_assets)
            run_process(command, cancel, log=output.parent / 'ffmpeg.log', cwd=work)
        else:
            render_sequential(program, [p.resolve() for p in images], audio.resolve(), Path('legendas.ass' if use_ass else 'legendas.srt'), work, candidate, duration, cancel,
                              output.parent / 'ffmpeg.log', fps, ass=use_ass, timeline=timeline)
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise RuntimeError('FFmpeg terminou sem criar o vídeo.')
        run_process([program, '-v', 'error', '-i', str(candidate), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], cancel)
        report = validate_final_video(program, candidate, duration, fps)
        profile_name, profile = resolve_mix_profile(mix_profile)
        report['caption_timing'] = 'word' if use_ass else 'sentence'
        report['captions'] = {
            'required_word_timing': bool(require_word_captions),
            'renderer': 'ass' if use_ass else 'subtitles',
            'events': ass_events if use_ass else 0,
            'source': 'legendas.ass' if use_ass else 'legendas.srt',
        }
        report['mix'] = {
            'profile': profile_name, 'configured_track': bool(music),
            'music_input_mixed': bool(music), 'parameters': profile if music else {},
        }
        report['audio_events'] = {
            'declared': len((timeline or {}).get('audio_events', [])),
            'rendered': len(event_assets),
            'omitted': [event.get('intent', 'unknown') for event in (timeline or {}).get('audio_events', []) if not event.get('asset')],
        }
        report['retention_preset'] = {
            'active': is_retention_preset((timeline or {}).get('preset')),
            'image_count': len(images),
            'expected_images': 6 if is_retention_preset((timeline or {}).get('preset')) else None,
        }
        write_report(report, output.parent / 'technical_report.json')
        if not report['valid']:
            raise RuntimeError('Validação técnica do MP4 falhou: ' + ' | '.join(report['failures']) + '. Consulte technical_report.json.')
        candidate.replace(output)
    return output
