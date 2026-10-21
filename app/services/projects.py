import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from app.core.models import Project, StepStatus
from app.database.repository import ProjectRepository

class ProjectService:
    def __init__(self, repo: ProjectRepository):
        self.repo = repo

    def artifact_dir(self, project: Project) -> Path:
        root = (self.repo.root / project.slug).resolve()
        if root.parent != self.repo.root:
            raise ValueError('Diretório do projeto inválido.')
        return root

    def artifact(self, project, relative):
        path = (self.artifact_dir(project) / relative).resolve()
        if not path.is_relative_to(self.artifact_dir(project)):
            raise ValueError('Mídia fora da pasta do projeto; importe novamente.')
        return path

    def cover_path(self, project: Project) -> Path:
        return self.artifact(project, 'capa.png')

    def existing_cover_path(self, project: Project) -> Path:
        """Prefer the current lossless cover while keeping old projects viewable."""
        current = self.cover_path(project)
        if current.is_file():
            return current
        legacy = self.artifact(project, 'capa.jpg')
        return legacy if legacy.is_file() else current

    def create_cover(self, project: Project, source: str | Path, *, headline: str = '', subheadline: str = '', brand: str = 'Lore Oculta',
                     preset: str = 'cinematico', effect_intensity: str = 'moderada', border: bool = True,
                     text_position: str = 'top', highlight_words: list[str] | None = None) -> Path:
        from app.services.cover import CoverService

        cover_keys = ('cover_source', 'cover_headline', 'cover_subheadline', 'cover_brand', 'cover_preset',
                      'cover_effect_intensity', 'cover_border', 'cover_text_position', 'cover_highlight_words')
        previous = {key: project.settings.get(key) for key in cover_keys}
        present = {key: key in project.settings for key in cover_keys}
        cover_path = self.cover_path(project)
        previous_cover = cover_path.read_bytes() if cover_path.is_file() else None
        cover = CoverService(self).generate(project, source, headline, subheadline, brand, preset=preset,
                                            effect_intensity=effect_intensity, border=border,
                                            text_position=text_position, highlight_words=highlight_words)
        project.settings['cover_source'] = str(source)
        project.settings['cover_headline'] = headline
        project.settings['cover_subheadline'] = subheadline
        project.settings['cover_brand'] = brand
        project.settings['cover_preset'] = preset
        project.settings['cover_effect_intensity'] = effect_intensity
        project.settings['cover_border'] = bool(border)
        project.settings['cover_text_position'] = text_position
        project.settings['cover_highlight_words'] = list(highlight_words or [])
        try:
            self.repo.save(project)
        except Exception:
            for key in cover_keys:
                if present[key]:
                    project.settings[key] = previous[key]
                else:
                    project.settings.pop(key, None)
            if previous_cover is None:
                cover.unlink(missing_ok=True)
            else:
                restore = cover.with_name(f'.{cover.stem}-{uuid.uuid4().hex}.restore')
                try:
                    restore.write_bytes(previous_cover)
                    restore.replace(cover)
                finally:
                    restore.unlink(missing_ok=True)
            raise
        return cover

    def set_status(self, project: Project, step: str, status: StepStatus, message=''):
        project.statuses[step] = status.value
        project.history.append(dict(time=datetime.now(timezone.utc).isoformat(), step=step, status=status.value, message=message))
        return self.repo.save(project)

    def reopen(self, id):
        project = self.repo.get(id)
        required = {'voz': ['narracao.wav'], 'legendas': ['legendas.srt', 'legendas.ass'], 'video': ['video_final.mp4'], 'imagens': project.images}
        for step, status in list(project.statuses.items()):
            if status == StepStatus.PROCESSING:
                self.set_status(project, step, StepStatus.ERROR, 'Execução interrompida. Execute novamente.')
            elif status == StepStatus.DONE and step in required:
                try:
                    valid = bool(required[step]) and all(self.artifact(project, name).is_file() for name in required[step])
                except ValueError:
                    valid = False
                if not valid:
                    self.set_status(project, step, StepStatus.ERROR, 'Artefato ausente. Importe ou gere novamente.')
        return project

    def add_images(self, project: Project, files: list[str]):
        from PySide6.QtGui import QImageReader
        sources = [Path(file) for file in files]
        root = self.artifact_dir(project)
        target = root / 'cenas'
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise OSError(f'Não foi possível acessar a pasta do projeto: {target}. {error.strerror or error}') from error
        imported = 0
        created = []
        original_images = list(project.images)
        original_statuses = dict(project.statuses)
        original_history = list(project.history)

        def destination_for(source):
            # O nome original pode exceder MAX_PATH quando combinado com a
            # pasta do projeto no Windows. A cópia interna continua legível,
            # única e com a extensão que o FFmpeg usa para identificar a mídia.
            stem = source.stem[:48].rstrip(' .') or 'imagem'
            suffix = source.suffix.lower()
            return target / f'{uuid.uuid4().hex[:8]}-{stem}{suffix}'

        def rollback_saved_batch():
            project.images[:] = original_images
            project.statuses.clear()
            project.statuses.update(original_statuses)
            project.history[:] = original_history
            for copied in created:
                copied.unlink(missing_ok=True)

        def persist_completed(message):
            project.statuses['video'] = StepStatus.PENDING
            try:
                return self.set_status(project, 'imagens', StepStatus.DONE, message)
            except Exception:
                rollback_saved_batch()
                raise

        for source in sources:
            copy = destination_for(source)
            temporary = copy.with_name(copy.name + '.part')
            try:
                if not source.is_file() or not QImageReader(str(source)).canRead():
                    raise OSError(f'Imagem inválida ou indisponível: {source.name}')
                # Arquivos em nuvem/removíveis podem desaparecer após a seleção.
                shutil.copy2(source, temporary)
                if not QImageReader(str(temporary)).canRead():
                    raise ValueError('o arquivo copiado não é uma imagem legível')
                temporary.replace(copy)
                project.images.append(copy.relative_to(root).as_posix())
                created.append(copy)
                imported += 1
            except (OSError, ValueError) as error:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                if imported:
                    persist_completed(f'{imported} imagem(ns) importada(s); importação interrompida em {source.name}.')
                detail = error.strerror if isinstance(error, OSError) and error.strerror else str(error)
                raise OSError(f'Não foi possível importar "{source.name}": {detail}. Verifique se o arquivo ainda existe e está disponível localmente.') from error
        return persist_completed(f'{imported} imagem(ns) importada(s).')
