"""Gera amostras locais Kokoro PT-BR sem chamar o pipeline nem renderizar vídeo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import numpy as np
import soundfile as sf
from kokoro import KPipeline


TEXT = (
    "Você sabe quem é Hal Jordan, um dos Lanternas Verdes mais poderosos da DC? "
    "Antes de se tornar um herói intergaláctico, Hal Jordan era um piloto de testes "
    "conhecido por sua coragem e imprudência."
)
SAMPLE_RATE = 24_000
DEFAULT_VOICES = ("pm_alex", "pm_santa", "pf_dora")


def synthesize(pipeline: KPipeline, text: str, voice: str, speed: float) -> np.ndarray:
    chunks = [audio for _, _, audio in pipeline(text, voice=voice, speed=speed, split_pattern=r"\n+")]
    if not chunks:
        raise RuntimeError("Kokoro não retornou áudio para a amostra.")
    return np.concatenate(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("verification/kokoro"))
    parser.add_argument("--voice", action="append", dest="voices", choices=DEFAULT_VOICES)
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()
    if not 0.6 <= args.speed <= 1.5:
        parser.error("--speed deve estar entre 0.6 e 1.5")

    args.output.mkdir(parents=True, exist_ok=True)
    report = {"text": TEXT, "language": "pt-BR", "voices": [], "python": sys.version}
    pipeline = KPipeline(lang_code="p")
    for voice in args.voices or DEFAULT_VOICES:
        started = perf_counter()
        record = {"voice": voice, "speed": args.speed, "sample_rate": SAMPLE_RATE}
        try:
            audio = synthesize(pipeline, TEXT, voice, args.speed)
            target = args.output / f"{voice}.wav"
            sf.write(target, audio, SAMPLE_RATE)
            record.update(
                status="ok",
                wav=str(target.resolve()),
                duration_seconds=round(len(audio) / SAMPLE_RATE, 3),
                elapsed_seconds=round(perf_counter() - started, 3),
            )
        except Exception as error:
            record.update(status="error", error=str(error), elapsed_seconds=round(perf_counter() - started, 3))
        report["voices"].append(record)

    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(report_path.resolve())
    return 0 if all(item["status"] == "ok" for item in report["voices"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
