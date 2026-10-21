from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
import pytest

from app.core.models import Project, StepStatus
from app.database.repository import ProjectRepository
from app.services.projects import ProjectService


def project_service(tmp_path):
    repo = ProjectRepository(tmp_path / 'db.sqlite', tmp_path / 'projects')
    project = repo.save(Project(None, '', 'Tema', 'Games', 'Short', 45, 'Texto.'))
    return repo, project, ProjectService(repo)


@pytest.fixture(scope='module', autouse=True)
def qapplication():
    return QApplication.instance() or QApplication([])


def image(path, width=400, height=200):
    value = QImage(width, height, QImage.Format.Format_RGB32)
    value.fill(0xff4c1d95)
    assert value.save(str(path))
    return path


def test_create_cover_is_vertical_persisted_and_leaves_video_artifacts_untouched(tmp_path):
    repo, project, service = project_service(tmp_path)
    source = image(tmp_path / 'original.png')
    video = service.artifact(project, 'video_final.mp4')
    audio = service.artifact(project, 'narracao.wav')
    subtitles = service.artifact(project, 'legendas.srt')
    video.write_bytes(b'video original')
    audio.write_bytes(b'audio original')
    subtitles.write_text('legenda original', encoding='utf8')
    project.statuses = {'voz': StepStatus.DONE, 'legendas': StepStatus.DONE, 'video': StepStatus.DONE}
    repo.save(project)

    cover = service.create_cover(project, source)

    generated = QImage(str(cover))
    assert cover == service.cover_path(project)
    assert generated.size().width() == 1080 and generated.size().height() == 1920
    assert repo.get(project.id).settings['cover_source'] == str(source)
    assert video.read_bytes() == b'video original'
    assert audio.read_bytes() == b'audio original'
    assert subtitles.read_text(encoding='utf8') == 'legenda original'
    assert repo.get(project.id).statuses == project.statuses
    repo.cx.close()


def test_create_cover_persists_reviewed_copy_without_touching_video_artifacts(tmp_path):
    repo, project, service = project_service(tmp_path)
    source = image(tmp_path / 'original.png')
    video = service.artifact(project, 'video_final.mp4')
    video.write_bytes(b'video original')

    cover = service.create_cover(
        project, source, headline='AS 3 BRUXAS', subheadline='QUEM SÃO ELAS?', brand='Lore Oculta',
    )

    generated = QImage(str(cover))
    saved = repo.get(project.id)
    assert generated.size().width() == 1080 and generated.size().height() == 1920
    assert saved.settings['cover_headline'] == 'AS 3 BRUXAS'
    assert saved.settings['cover_subheadline'] == 'QUEM SÃO ELAS?'
    assert saved.settings['cover_brand'] == 'Lore Oculta'
    assert video.read_bytes() == b'video original'
    repo.cx.close()


def test_create_cover_exports_lossless_png_with_reviewed_composition(tmp_path):
    repo, project, service = project_service(tmp_path)
    source = image(tmp_path / 'source.png', 300, 600)

    cover = service.create_cover(
        project, source, headline='A VERDADE OCULTA', subheadline='O QUE NINGUÉM VIU?',
        preset='confronto', effect_intensity='intensa', border=True, text_position='middle', highlight_words=['VERDADE'],
    )

    generated = QImage(str(cover))
    saved = repo.get(project.id)
    assert cover.suffix == '.png'
    assert generated.size().width() == 1080 and generated.size().height() == 1920
    assert saved.settings['cover_preset'] == 'confronto'
    assert saved.settings['cover_effect_intensity'] == 'intensa'
    assert saved.settings['cover_text_position'] == 'middle'
    assert saved.settings['cover_border'] is True
    assert saved.settings['cover_highlight_words'] == ['VERDADE']
    repo.cx.close()


def test_cover_preserves_individual_highlight_words_without_changing_video_artifacts(tmp_path):
    repo, project, service = project_service(tmp_path)
    source = image(tmp_path / 'source.png', 1280, 720)
    video = service.artifact(project, 'video_final.mp4')
    video.write_bytes(b'video original')

    cover = service.create_cover(
        project, source, headline='O BATMAN TRAIU TODOS',
        preset='confronto', effect_intensity='intensa', text_position='top',
        highlight_words=['BATMAN'],
    )

    rendered = QImage(str(cover))
    yellow_pixels = 0
    white_pixels = 0
    for y in range(80, 680, 20):
        for x in range(40, 1040, 20):
            color = rendered.pixelColor(x, y)
            yellow_pixels += int(color.red() > 190 and color.green() > 150 and color.blue() < 90)
            white_pixels += int(min(color.red(), color.green(), color.blue()) > 170)
    assert yellow_pixels > 0 and white_pixels > 0
    assert video.read_bytes() == b'video original'
    repo.cx.close()


def test_cover_rejects_copy_that_would_hide_the_visual_with_excessive_text(tmp_path):
    repo, project, service = project_service(tmp_path)
    source = image(tmp_path / 'source.png')

    with pytest.raises(ValueError, match='máximo seis'):
        service.create_cover(project, source, headline='ESTE TÍTULO TEM PALAVRAS DEMAIS PARA UMA CAPA CLARA')

    repo.cx.close()


def test_invalid_or_missing_cover_source_preserves_last_valid_cover(tmp_path):
    repo, project, service = project_service(tmp_path)
    valid = image(tmp_path / 'valid.png')
    cover = service.create_cover(project, valid)
    original = cover.read_bytes()
    invalid = tmp_path / 'invalid.png'
    invalid.write_text('não é imagem', encoding='utf8')

    with pytest.raises(ValueError, match='inválida'):
        service.create_cover(project, invalid)
    with pytest.raises(ValueError, match='inexistente'):
        service.create_cover(project, tmp_path / 'ausente.jpg')

    assert cover.read_bytes() == original
    assert repo.get(project.id).settings['cover_source'] == str(valid)
    repo.cx.close()


def test_cover_and_selection_are_restored_when_persistence_fails(tmp_path, monkeypatch):
    repo, project, service = project_service(tmp_path)
    original_source = image(tmp_path / 'original.png')
    replacement_source = image(tmp_path / 'replacement.png', 200, 400)
    cover = service.create_cover(project, original_source)
    original_cover = cover.read_bytes()

    def fail_save(_):
        raise OSError('Banco temporariamente indisponível')

    monkeypatch.setattr(repo, 'save', fail_save)
    with pytest.raises(OSError, match='Banco'):
        service.create_cover(project, replacement_source)

    assert cover.read_bytes() == original_cover
    assert project.settings['cover_source'] == str(original_source)
    assert repo.get(project.id).settings['cover_source'] == str(original_source)
    repo.cx.close()
