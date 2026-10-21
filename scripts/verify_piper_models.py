"""Gera amostras diretas do Piper, sem pipeline ou interface."""
import json
from pathlib import Path

from app.providers.piper import PiperProvider
from app.services.subtitles import duration_wav

ROOT = Path(__file__).resolve().parents[1]
TEXT = 'Você sabe quem é Hal Jordan? Ele era um piloto de testes conhecido por sua coragem e imprudência. Seu anel permite criar construções usando a força de vontade.'


def main():
    output_dir = ROOT / 'verification' / 'piper-isolado'
    output_dir.mkdir(parents=True, exist_ok=True)
    executable = ROOT / '.venv' / 'Scripts' / 'piper.exe'
    results = []
    for name in ('pt_BR-faber-medium.onnx', 'pt_BR-cadu-medium.onnx'):
        model = ROOT / 'models' / 'piper' / name
        if not model.is_file() or not Path(str(model) + '.json').is_file():
            continue
        config = json.loads(Path(str(model) + '.json').read_text(encoding='utf8'))
        if config.get('language', {}).get('code') != 'pt_BR':
            raise ValueError(f'Modelo incompatível com PT-BR: {model.name}')
        target = output_dir / f'{model.stem}.wav'
        PiperProvider(str(executable), str(model)).synthesize(TEXT, target, speed=1.0, sentence_pause=.3)
        process = json.loads((output_dir / 'piper.log').read_text(encoding='utf8'))
        results.append({'model': str(model), 'model_language': config['language']['code'], 'piper_version': config.get('piper_version'), 'text': TEXT, 'command': process['command'], 'returncode': process['returncode'], 'wav': str(target), 'duration_seconds': duration_wav(target)})
    report = output_dir / 'report.json'
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')
    print(report)


if __name__ == '__main__':
    main()
