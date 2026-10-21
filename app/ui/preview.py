"""Reprodução local das saídas concluídas do projeto."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider


class Preview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.message = QLabel('Selecione uma narração ou um vídeo concluído para assistir aqui.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.video = QVideoWidget()
        self.video.setMinimumSize(240, 240)
        layout.addWidget(self.video, 1)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        controls = QHBoxLayout()
        self.play = QPushButton('Reproduzir / pausar')
        self.play.setEnabled(False)
        self.play.clicked.connect(self.toggle)
        controls.addWidget(self.play)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self.player.setPosition)
        controls.addWidget(self.seek, 1)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setMaximumWidth(120)
        self.volume.setAccessibleName('Volume da prévia')
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        controls.addWidget(QLabel('Volume'))
        controls.addWidget(self.volume)
        layout.addLayout(controls)
        self.player.durationChanged.connect(lambda duration: self.seek.setRange(0, duration))
        self.player.positionChanged.connect(self.seek.setValue)
        self.player.errorOccurred.connect(self.failed)

    def open(self, path):
        path = Path(path).resolve()
        self.clear()
        if not path.is_file():
            raise FileNotFoundError('Gere o arquivo antes de abrir a prévia.')
        self.message.setText(path.name)
        self.video.setVisible(path.suffix.lower() != '.wav')
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.play.setEnabled(True)

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def failed(self, error, message):
        self.message.setText('Não foi possível reproduzir: ' + message)
        self.play.setEnabled(False)

    def clear(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.play.setEnabled(False)
        self.seek.setRange(0, 0)
        self.message.setText('Selecione uma saída concluída do projeto.')
