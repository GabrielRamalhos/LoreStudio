"""Verificação explícita com Piper configurado e FFmpeg local; sem mocks."""
import argparse
from pathlib import Path
import subprocess
from PySide6.QtGui import QColor, QImage
from app.providers.piper import PiperProvider
from app.services.subtitles import duration_wav, write_srt
from app.services.video import render


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--piper', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--output', default='verification/real-pipeline')
    parser.add_argument('--motion', action='store_true', help='Ativar zoom suave')
    parser.add_argument('--transition', action='store_true', help='Ativar dissolução entre cenas')
    parser.add_argument('--music', help='Caminho da trilha local opcional')
    parser.add_argument('--music-volume', type=float, default=0.15, help='Volume da trilha entre 0 e 1')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    text = 'Bem-vindo ao Lore Oculta. Toda história guarda um segredo. Vamos descobrir juntos.'
    audio = PiperProvider(args.piper, args.model).synthesize(text, output / 'narracao.wav')
    images = []
    for index, color in enumerate(('#30214c', '#173e4e')):
        image = QImage(540, 960, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        path = output / f'cena-{index + 1}.png'
        if not image.save(str(path)):
            raise RuntimeError('Não foi possível gravar o fundo sintético.')
        images.append(path)
    subtitles = write_srt(text, audio, output / 'legendas.srt')
    video = render(args.ffmpeg, images, audio, subtitles, output / 'video_final.mp4',
                   motion=args.motion, transition=args.transition, music=args.music, music_volume=args.music_volume)
    result = subprocess.run([args.ffmpeg, '-v', 'error', '-i', str(video), '-f', 'null', '-'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    print(f'Piper real: {duration_wav(audio):.2f}s; MP4 decodificado: {video}')


if __name__ == '__main__':
    main()
