"""Produz os cinco Shorts aprovados com os ativos locais fornecidos pelo usuário.

Uso: python scripts/produzir_cinco_shorts.py
Os projetos e artefatos são gravados na pasta de dados do aplicativo, não no
repositório. As imagens de cada pasta são usadas por data de salvamento.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from threading import Event

from app.core.models import Project
from app.database.repository import ProjectRepository
from app.services.music import copy_music
from app.services.paths import migrate_legacy_data
from app.services.pipeline import Pipeline
from app.services.projects import ProjectService
from app.services.settings import SettingsStore, resolved_mapping
from app.services.subtitles import duration_wav


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "imagens"
TRACK = ASSETS / "leberch-suspense-cinematic-581368.mp3"

JOBS = (
    {
        "theme": "O VISÃO pode se tornar o próximo ULTRON?",
        "title": "O VISÃO pode se tornar o próximo ULTRON? 😱",
        "category": "Marvel",
        "folder": "ultron",
        "description": "O Visão Branco pode escapar do legado de Ultron? Entenda essa conexão misteriosa da Marvel.",
        "script": """E se o maior inimigo do Visão também fosse parte da sua própria origem?

O Visão foi criado com a ajuda de uma tecnologia que nasceu de Ultron, uma das inteligências artificiais mais perigosas da Marvel.

E agora, depois de ser reconstruído, ele voltou com um corpo branco e uma identidade completamente diferente.

Em WandaVision, suas memórias foram desbloqueadas.

Mas lembrar do passado não significa recuperar tudo o que ele era.

E com Ultron retornando à história, uma pergunta fica no ar:

Será que o Visão conseguirá escapar do legado de seu próprio criador?

Ou esse passado será sua maior ameaça?

Curta e se inscreva no Lore Oculta para mais segredos da Marvel!""",
    },
    {
        "theme": "O LANTERNA VERDE já virou um VILÃO?",
        "title": "O LANTERNA VERDE já virou um VILÃO? 😱",
        "category": "DC Comics",
        "folder": "HALL",
        "description": "A história de Hal Jordan como Parallax nos quadrinhos e a dúvida sobre o lado sombrio de Lanternas.",
        "script": """E se o maior Lanterna Verde da Terra não fosse o herói que todos esperavam?

Hal Jordan foi escolhido por um anel capaz de transformar sua força de vontade em poder.

Mas existe um detalhe sombrio na história dele!

Nos quadrinhos, Hal perdeu o controle após a destruição de Coast City.

Consumido pelo desespero, ele enfrentou outros Lanternas e assumiu a identidade de Parallax.

Um dos maiores heróis da DC acabou se tornando uma ameaça!

E com Hal Jordan ganhando destaque novamente em Lanternas, muita gente quer saber:

Será que a série vai explorar seu lado mais sombrio?

Curta e se inscreva no Lore Oculta para mais histórias da DC!""",
    },
    {
        "theme": "GTA 6 esconde MUITO MAIS que Vice City!",
        "title": "GTA 6 esconde MUITO MAIS que Vice City! 😱",
        "category": "Games",
        "folder": "GTA",
        "description": "Vice City é só parte de Leonida: veja por que o mundo de GTA 6 parece muito maior.",
        "script": """Você percebeu que GTA 6 não vai mostrar apenas Vice City?

A Rockstar está levando os jogadores para Leonida, um estado inteiro inspirado na Flórida!

E isso muda muita coisa.

Além das praias e das luzes de Vice City, o jogo apresenta pântanos, pequenas cidades, estradas e regiões selvagens.

Ou seja, o mundo de Lucia e Jason vai muito além daquela cidade que conhecemos em GTA Vice City.

Mas aqui vem a pergunta:

Quantos segredos a Rockstar escondeu nesses novos lugares?

Será que vamos encontrar referências a personagens dos jogos antigos?

Curta e se inscreva no Lore Oculta para mais segredos de GTA 6!""",
    },
    {
        "theme": "O vilão do BATMAN que pode ROUBAR seu rosto!",
        "title": "O vilão do BATMAN que pode ROUBAR seu rosto! 😱",
        "category": "DC Comics",
        "folder": "BATMAN",
        "description": "Cara-de-Barro pode copiar rostos e transformar o próprio corpo em uma criatura de lama.",
        "script": """E se você encontrasse alguém com o seu rosto... mas não fosse você?

Esse é um dos poderes mais assustadores do Cara-de-Barro, um dos vilões mais perigosos do Batman!

Em diferentes versões dos quadrinhos, ele consegue transformar seu corpo e assumir a aparência de outras pessoas.

Imagine enfrentar um inimigo que pode copiar praticamente qualquer rosto!

Você nunca saberia em quem confiar.

E o pior: dependendo da versão, seu corpo pode se transformar em uma criatura gigantesca feita de lama!

Mas existe algo ainda mais assustador...

Por trás desse monstro, muitas histórias mostram um homem que perdeu sua própria identidade.

Curta e se inscreva no Lore Oculta para mais segredos da DC!""",
    },
    {
        "theme": "DOUTOR DESTINO é mais FORTE que THANOS?",
        "title": "DOUTOR DESTINO é mais FORTE que THANOS? 😱",
        "category": "Marvel",
        "folder": "DESTINO",
        "description": "Doutor Destino contra Thanos: ciência, magia e o poder que ele alcançou em Guerras Secretas.",
        "script": """E se eu te dissesse que existe um vilão da Marvel capaz de ser ainda mais perigoso que Thanos?

Estamos falando do Doutor Destino!

Enquanto Thanos ficou conhecido por usar as Joias do Infinito, Victor Von Doom combina ciência, magia e uma inteligência extraordinária.

E nos quadrinhos, ele já conseguiu alcançar um poder praticamente divino!

Durante Guerras Secretas, Destino se tornou uma das figuras mais poderosas do multiverso.

Mas o que torna esse vilão tão perigoso não é apenas sua força.

É sua capacidade de planejar, manipular e acreditar que somente ele pode salvar o mundo!

Agora me diga:

Quem venceria: Doutor Destino ou Thanos?

Curta e se inscreva no Lore Oculta para mais batalhas da Marvel!""",
    },
)


def ordered_images(folder: Path) -> list[str]:
    images = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".jfif"]
    images.sort(key=lambda path: (path.stat().st_ctime_ns, path.stat().st_mtime_ns, path.name.lower()))
    if len(images) != 6:
        raise RuntimeError(f"{folder.name}: eram esperadas 6 imagens JFIF; encontradas {len(images)}.")
    return [str(path) for path in images]


def publication_text(job: dict, duration: float, image_names: list[str]) -> str:
    return "\n".join((
        f"Título: {job['title']}",
        "",
        f"Descrição: {job['description']}",
        "",
        "#LoreOculta #Shorts",
        "",
        f"Duração natural da narração: {duration:.2f} s",
        "Imagens: " + ", ".join(image_names),
        "Trilha: leberch-suspense-cinematic-581368.mp3",
        "Origem da trilha: fornecida pelo usuário.",
        "Licença da trilha: não informada pelo usuário; confirmar antes da publicação comercial.",
        "",
    ))


def main() -> None:
    if not TRACK.is_file():
        raise FileNotFoundError(f"Trilha não encontrada: {TRACK}")
    paths = migrate_legacy_data()
    settings = resolved_mapping(asdict(SettingsStore(paths.settings).load()), ROOT)
    repository = ProjectRepository(paths.database, paths.projects)
    service = ProjectService(repository)
    pipeline = Pipeline(service)
    cancel = Event()

    print(f"Dados do aplicativo: {paths.root}", flush=True)
    print(f"TTS selecionado: {settings.get('tts_provider')} / {settings.get('kokoro_voice') or settings.get('piper_model')}", flush=True)
    for index, job in enumerate(JOBS, 1):
        print(f"[{index}/5] Criando: {job['theme']}", flush=True)
        sources = ordered_images(ASSETS / job["folder"])
        project = next((item for item in repository.list() if item.theme == job["theme"] and item.script == job["script"]), None)
        if project is None:
            project = repository.save(Project(None, "", job["theme"], job["category"], "Short", 40, job["script"]))
            service.add_images(project, sources)
        elif len(project.images) != 6:
            raise RuntimeError(f"{project.slug}: projeto existente possui {len(project.images)} imagens, não 6.")
        root = service.artifact_dir(project)
        if project.statuses.get("video") == "CONCLUIDO" and (root / "video_final.mp4").is_file():
            print(f"[{index}/5] Já concluído: {root / 'video_final.mp4'}", flush=True)
            continue
        pipeline.execute(project, "voz", settings, cancel)
        duration = duration_wav(service.artifact(project, "narracao.wav"))
        project.audio_duration = duration
        relative_track, plan = copy_music(service, project, TRACK, settings["ffmpeg"])
        project.settings.update({
            "video_music": relative_track,
            "video_music_plan": plan,
            "video_music_volume": 12,
            "video_motion": False,
            "video_transition": True,
            "production_source_images": [Path(source).name for source in sources],
            "production_track_origin": "Fornecida pelo usuário",
            "production_track_license": "Não informada — confirmar antes da publicação comercial",
        })
        repository.save(project)
        (root / "publicacao.txt").write_text(publication_text(job, duration, [Path(source).name for source in sources]), encoding="utf8")
        print(f"[{index}/5] Narração e legendas prontas: {duration:.2f}s. Renderizando MP4...", flush=True)
        pipeline.execute(project, "video", settings, cancel)
        print(f"[{index}/5] Concluído: {root / 'video_final.mp4'}", flush=True)
    print("PRODUÇÃO CONCLUÍDA", flush=True)


if __name__ == "__main__":
    main()
