"""Produz o Short Superman Injustice com os ativos fornecidos pelo usuário.

Reutiliza criação de projeto, TTS Kokoro, ASR e validação do Lore Oculta
Studio. A composição final usa fundo desfocado da própria imagem para manter
todo o enquadramento sem barras pretas em mídias não verticais.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from threading import Event

from app.core.models import Project, StepStatus
from app.database.repository import ProjectRepository
from app.providers.process import executable, run_process
from app.services.paths import migrate_legacy_data
from app.services.pipeline import Pipeline
from app.services.projects import ProjectService
from app.services.settings import SettingsStore, resolved_mapping
from app.services.subtitles import duration_wav
from app.services.technical_validation import validate_video, write_report


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "imagens" / "superman"
TITLE = "O CORINGA transformou o SUPERMAN em um MONSTRO!"
SCRIPT = """O SUPERMAN MATOU O CORINGA!

Em Injustice, o Coringa enganou Superman e fez ele matar a própria Lois Lane.

A morte dela também provocou a destruição de Metrópolis.

Consumido pela dor, Superman matou o Coringa e decidiu impor a paz pela força.

Batman se recusou a aceitar aquilo. E os maiores heróis da Terra entraram em guerra.

Curta e se inscreva! Porque tudo começou quando..."""


def source_images() -> list[Path]:
    expected = [SOURCE_DIR / f"{index}.jfif" for index in range(1, 7)]
    missing = [image.name for image in expected if not image.is_file()]
    if missing:
        raise FileNotFoundError("Imagens obrigatórias ausentes: " + ", ".join(missing))
    return expected


def original_music(ffmpeg: str, target: Path, duration: float) -> Path:
    """Cria uma cama instrumental sintética local, sem conteúdo de terceiros."""
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable(ffmpeg), "-y", "-hide_banner", "-nostdin",
        "-f", "lavfi", "-i", f"sine=frequency=55:sample_rate=48000:duration={duration:.6f}",
        "-f", "lavfi", "-i", f"sine=frequency=110:sample_rate=48000:duration={duration:.6f}",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:sample_rate=48000:duration={duration:.6f}",
        "-filter_complex",
        "[0:a]volume=0.10,lowpass=f=120[bass];"
        "[1:a]volume=0.025,aecho=0.8:0.7:280:0.18[tension];"
        "[2:a]volume=0.006,lowpass=f=1100[texture];"
        "[bass][tension][texture]amix=inputs=3:normalize=0,afade=t=in:d=0.35[music]",
        "-map", "[music]", "-c:a", "pcm_s16le", str(target),
    ]
    run_process(command)
    return target


def render_vertical(ffmpeg: str, images: list[Path], narration: Path, music: Path, captions: Path, output: Path, duration: float) -> None:
    # Cena 1 recebe ~3s para o gancho; a distribuição restante segue a fala real.
    weights = (0.12, 0.18, 0.15, 0.20, 0.20, 0.15)
    durations = [duration * weight for weight in weights]
    command = [executable(ffmpeg), "-y", "-hide_banner", "-nostdin"]
    for image in images:
        command += ["-loop", "1", "-framerate", "30", "-i", str(image)]
    command += ["-i", str(narration), "-stream_loop", "-1", "-i", str(music)]
    filters: list[str] = []
    impact_texts = ("SUPERMAN MATOU?!", "ELE PERDEU LOIS!", "METRÓPOLIS DESTRUÍDA!", "NASCE UM TIRANO!", "HERÓIS EM GUERRA!", "TUDO COMEÇOU QUANDO...")
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    for index, scene_duration in enumerate(durations):
        # Fundo ampliado/desfocado + primeiro plano integral: sem crop do conteúdo principal.
        filters += [
            f"[{index}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:8[bg{index}]",
            f"[{index}:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg{index}]",
            f"[bg{index}][fg{index}]overlay=(W-w)/2:(H-h)/2,setsar=1,drawtext=fontfile='{font}':text='{impact_texts[index]}':x=(w-text_w)/2:y=150:fontsize=60:fontcolor=white:borderw=4:bordercolor=black,trim=duration={scene_duration:.9f},setpts=PTS-STARTPTS,fps=30[v{index}]",
        ]
    filters.append("".join(f"[v{index}]" for index in range(6)) + "concat=n=6:v=1:a=0[visual]")
    filters.append("[visual]subtitles=filename='legendas.srt',format=yuv420p,scale=in_range=pc:out_range=tv[video]")
    filters.append(f"[7:a]atrim=duration={duration:.9f},asetpts=PTS-STARTPTS,loudnorm=I=-25:LRA=7:TP=-2,volume=0.10[music]")
    filters.append("[6:a][music]sidechaincompress=threshold=0.025:ratio=14:attack=15:release=280[ducked]")
    filters.append("[6:a][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0:latency=1[audio]")
    command += [
        "-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]",
        "-t", f"{duration:.9f}", "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-movflags", "+faststart", str(output),
    ]
    run_process(command, log=output.parent / "ffmpeg.log", cwd=output.parent)


def fps_of(ffmpeg: str, video: Path) -> str:
    probe = Path(executable(ffmpeg)).with_name("ffprobe.exe")
    if not probe.is_file():
        return "não verificado (ffprobe indisponível)"
    raw = subprocess.check_output([str(probe), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "default=nk=1:nw=1", str(video)], text=True, encoding="utf8")
    return raw.strip()


def main() -> None:
    paths = migrate_legacy_data()
    settings = resolved_mapping(asdict(SettingsStore(paths.settings).load()), ROOT)
    # Voz masculina local configurada e ritmo um pouco mais cadenciado, sem acelerar frases.
    settings.update(tts_provider="kokoro", kokoro_voice="pm_alex", narration_speed=0.90, sentence_pause=0.0)
    repo = ProjectRepository(paths.database, paths.projects)
    service = ProjectService(repo)
    pipeline = Pipeline(service)
    sources = source_images()
    project = next((item for item in repo.list() if item.theme == TITLE and item.script == SCRIPT), None)
    if project is None:
        project = repo.save(Project(None, "", TITLE, "DC Comics", "Short", 35, SCRIPT))
        service.add_images(project, [str(image) for image in sources])
    if len(project.images) != 6:
        raise RuntimeError("O projeto Superman não contém exatamente seis imagens.")
    root = service.artifact_dir(project)
    if not os.environ.get("LORE_FORCE_RENDER") and project.statuses.get("video") == StepStatus.DONE and (root / "video_final.mp4").is_file():
        print(root / "video_final.mp4")
        return
    narration = root / "narracao.wav"
    captions = root / "legendas.srt"
    if project.statuses.get("voz") != StepStatus.DONE or not narration.is_file() or not captions.is_file():
        pipeline.execute(project, "voz", settings, Event())
    duration = duration_wav(narration)
    music = original_music(settings["ffmpeg"], root / "trilha" / "superman-injustice-original.wav", duration)
    render_vertical(settings["ffmpeg"], [service.artifact(project, image) for image in project.images], narration, music, captions, root / "video_final.mp4", duration)
    report = validate_video(settings["ffmpeg"], root / "video_final.mp4", duration)
    report["production_checks"] = {
        "fps": fps_of(settings["ffmpeg"], root / "video_final.mp4"),
        "images_in_order": [image.name for image in sources],
        "loop": "O vídeo termina em 'Porque tudo começou quando...' sem fade ou silêncio adicionado; o loop da plataforma retorna ao gancho inicial.",
        "music": "Instrumental sintético original gerado localmente para este Short; sem material de terceiros.",
    }
    write_report(report, root / "technical_report.json")
    if not report["valid"]:
        raise RuntimeError("A validação técnica falhou: " + " | ".join(report["failures"]))
    project.audio_duration = duration
    project.settings.update({
        "video_music": "trilha/superman-injustice-original.wav",
        "video_music_volume": 10,
        "video_motion": False,
        "video_transition": False,
        "production_source_images": [image.name for image in sources],
        "production_track_origin": "Gerada localmente por síntese no FFmpeg",
        "production_track_license": "Original do projeto; sem material de terceiros",
    })
    service.set_status(project, "video", StepStatus.DONE, "video_final.mp4 | 30 FPS | composição vertical com fundo desfocado")
    (root / "publicacao.txt").write_text(
        "Título: O CORINGA transformou o SUPERMAN em um MONSTRO! 😱\n\n"
        "Descrição: Em Injustice, uma tragédia muda Superman para sempre. Batman consegue impedir o Regime?\n\n"
        "#LoreOculta #Superman #Injustice #DC #Shorts\n\n"
        f"Duração da narração: {duration:.2f} s\n"
        "Trilha: superman-injustice-original.wav — instrumental sintético original gerado localmente.\n",
        encoding="utf8",
    )
    print(root / "video_final.mp4")


if __name__ == "__main__":
    main()
