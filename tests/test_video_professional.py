from pathlib import Path
import wave
import imageio_ffmpeg

from app.services.video import professional_compose_filter, render_audio_preview, styled_command


def test_professional_composition_preserves_foreground_aspect_ratio():
    filters = professional_compose_filter()
    assert 'force_original_aspect_ratio=decrease' in filters
    assert 'crop=' not in filters


def test_timeline_motion_compiles_at_configured_fps(tmp_path):
    command = styled_command('ffmpeg', [tmp_path / 'one.png'], tmp_path / 'voz.wav', tmp_path / 'out.mp4',
                             2, True, False, None, .12, fps=24,
                             timeline={'clips': [{'motion': 'slow_push'}]})
    graph = command[command.index('-filter_complex') + 1]
    assert 'fps=24' in graph
    assert '-pix_fmt' in command and 'yuv420p' in command


def test_cinematic_mix_profile_keeps_loudness_ducking_and_limiter(tmp_path):
    command = styled_command('ffmpeg', [tmp_path / 'one.png'], tmp_path / 'voz.wav', tmp_path / 'out.mp4',
                             2, True, False, tmp_path / 'trilha.wav', .18, fps=30,
                             timeline={'clips': [{'motion': 'impact_zoom'}]}, mix_profile='cinematografica')
    graph = command[command.index('-filter_complex') + 1]
    assert 'loudnorm=I=-24' in graph
    assert 'sidechaincompress=threshold=0.028000:ratio=7' in graph
    assert 'alimiter=limit=0.95' in graph


def test_asset_backed_audio_event_is_delayed_and_mixed(tmp_path):
    command = styled_command('ffmpeg', [tmp_path / 'one.png'], tmp_path / 'voz.wav', tmp_path / 'out.mp4',
                             2, True, False, None, .12, fps=30,
                             timeline={'clips': [{'motion': 'impact_zoom'}]},
                             event_assets=[{'path': tmp_path / 'impact.wav', 'time': .4, 'gain': .3}])
    graph = command[command.index('-filter_complex') + 1]
    assert 'adelay=400|400' in graph
    assert 'volume=0.300000' in graph
    assert str(tmp_path / 'impact.wav') in command


def test_audio_preview_renders_a_local_playable_mix(tmp_path):
    narration = tmp_path / 'voz.wav'
    track = tmp_path / 'trilha.wav'
    for path in (narration, track):
        with wave.open(str(path), 'wb') as item:
            item.setnchannels(1); item.setsampwidth(2); item.setframerate(8000)
            item.writeframes(b'\0\0' * 8000)
    result = render_audio_preview(imageio_ffmpeg.get_ffmpeg_exe(), narration, tmp_path / 'previa.wav',
                                  music=track, music_volume=.25, mix_profile='equilibrada')
    assert result.is_file() and result.stat().st_size > 44
