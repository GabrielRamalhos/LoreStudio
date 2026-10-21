import os
import wave
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer
from app.ui.preview import Preview


def test_preview_loads_real_wav_and_releases_source(tmp_path):
    application = QApplication.instance() or QApplication([])
    path = tmp_path / 'narração.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 16000)
    preview = Preview()
    loop = QEventLoop()
    preview.player.durationChanged.connect(loop.quit)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    preview.open(path)
    timer.start(5000)
    if not preview.player.duration():
        loop.exec()
    timer.stop()
    assert preview.player.duration() == 1000
    assert preview.play.isEnabled()
    assert Path(preview.player.source().toLocalFile()).resolve() == path.resolve()
    preview.clear()
    assert preview.player.source().isEmpty()
    assert not preview.play.isEnabled()
    with pytest.raises(FileNotFoundError):
        preview.open(tmp_path / 'absent.mp4')
    assert preview.player.source().isEmpty()
    preview.close()


def test_preview_decodes_video_frame(tmp_path):
    import subprocess
    import imageio_ffmpeg
    application = QApplication.instance() or QApplication([])
    video = tmp_path / 'preview.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=purple:s=1080x1920:r=25:d=0.4', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(video)], check=True, capture_output=True)
    preview = Preview()
    loop = QEventLoop()
    frames = []
    def frame_received(frame):
        if frame.isValid():
            frames.append((frame.width(), frame.height()))
            loop.quit()
    preview.video.videoSink().videoFrameChanged.connect(frame_received)
    preview.open(video)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(5000)
    preview.player.play()
    if not frames:
        loop.exec()
    timer.stop()
    preview.clear()
    preview.close()
    assert frames and frames[0] == (1080, 1920)
