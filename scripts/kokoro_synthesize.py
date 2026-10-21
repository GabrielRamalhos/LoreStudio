"""Processo auxiliar do Kokoro TTS para manter o runtime Python 3.12 isolado."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--voice", choices=("pm_alex", "pm_santa", "pf_dora"), required=True)
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()
    if not 0.6 <= args.speed <= 1.5:
        parser.error("--speed deve estar entre 0.6 e 1.5")
    text = args.input.read_text(encoding="utf8").strip()
    if not text:
        parser.error("o texto de narração está vazio")

    pipeline = KPipeline(lang_code="p")
    parts = [audio for _, _, audio in pipeline(text, voice=args.voice, speed=args.speed, split_pattern=r"\n+")]
    if not parts:
        raise RuntimeError("Kokoro não retornou áudio.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.output, np.concatenate(parts), 24_000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
