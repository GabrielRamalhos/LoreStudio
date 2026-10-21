# Release procedure

The base Windows installer is published by Gabriel Ramalhos; support: gabriel.ramalhos97@gmail.com.

1. Obtain the approved Kokoro runtime/weights and an LGPL-only FFmpeg binary. Place them under `packaging/vendor/kokoro` and `packaging/vendor/ffmpeg/ffmpeg.exe`. Do not use the development `imageio-ffmpeg` binary without auditing its build configuration.
2. Record exact version, immutable source URL and SHA-256 for every delivered file in `COMPONENT_MANIFEST.json`; replace each `generated-at-release` placeholder. Include the corresponding license texts in `THIRD_PARTY_NOTICES.txt` or its `licenses/` folder.
3. Confirm that Piper, its voices, Ollama, ComfyUI, SD checkpoints, `projects/`, `config/settings.json` and `lore_oculta.db` are absent from the build inputs.
4. Run `powershell -ExecutionPolicy Bypass -File packaging/build.ps1` on a prepared Windows build machine. The script rejects missing compliance inputs and FFmpeg builds with `--enable-gpl`.
5. Sign both `dist/LoreOcultaStudio/LoreOcultaStudio.exe` and the generated installer with the publisher certificate, then verify signatures and hashes on a clean Windows VM.
6. Test a clean install and an update over existing `%LOCALAPPDATA%\Gabriel Ramalhos\Lore Oculta Studio`; settings/projects must remain untouched. The installer updates only its Program Files directory.

This document is release guidance, not a legal opinion or legal review.
