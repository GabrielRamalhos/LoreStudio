import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QScrollArea
from app.main import Window
from app.services.editorial_director import stable_input_hash


def test_dashboard_is_the_automatic_short_entrypoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window()
    assert window.tabs.currentWidget() is window.dashboard_page
    assert window.quick_theme.placeholderText()
    assert window.quick_script.placeholderText()
    assert 'imagens' in window.automatic_progress.text()
    window.close()
    window.repo.cx.close()


def test_retention_preset_is_available_in_both_existing_selectors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window()
    label = 'Lore Oculta — Retenção Máxima'
    assert window.quick_preset.findText(label) >= 0
    assert window.editing_preset.findText(label) >= 0
    window.editing_preset.setCurrentText(label)
    assert window.music_mix_profile.currentData() == 'cinematografica'


def test_narrative_presets_and_default_music_volume_are_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window()
    for label in ('História sombria', 'Top 5', 'Teoria e mistério'):
        assert window.quick_preset.findText(label) >= 0
        assert window.editing_preset.findText(label) >= 0
    assert window.music_volume.value() == 25
    assert window.effects_intensity.currentData() == 'moderada'
    window.close(); window.repo.cx.close()


def test_media_page_scrolls_and_hides_advanced_sections_until_requested(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window()
    assert isinstance(window.media_page, QScrollArea)
    assert window.manual_timeline_body.isHidden()
    assert window.advanced_media_body.isHidden()
    assert window.automatic_assembly.title() == 'Revisar montagem com IA (opcional)'
    assert window.automatic_assembly_body.isHidden()
    assert window.cover_simple_controls.title() == 'Capa do Short (opcional)'
    assert window.cover_custom_body.isHidden()
    window.manual_timeline_controls.setChecked(True)
    window.advanced_media_controls.setChecked(True)
    assert not window.manual_timeline_body.isHidden()
    assert not window.advanced_media_body.isHidden()
    window.close(); window.repo.cx.close()


def test_editor_keeps_the_automatic_action_primary_and_technical_actions_collapsed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window()
    assert window.advanced_creation_controls.title() == 'Ações técnicas (opcional)'
    assert window.advanced_creation_body.isHidden()
    window.advanced_creation_controls.setChecked(True)
    assert not window.advanced_creation_body.isHidden()
    window.close(); window.repo.cx.close()


def test_primary_pages_scroll_and_preview_is_a_real_tab(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window()
    assert isinstance(window.dashboard_page, QScrollArea)
    assert isinstance(window.config_page, QScrollArea)
    assert window.tabs.indexOf(window.preview_page) >= 0
    window.tabs.setCurrentWidget(window.preview_page)
    assert window.tabs.currentWidget() is window.preview_page
    window.close(); window.repo.cx.close()

def test_edit_preserves_media_and_failed_save_does_not_return_stale_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window()
    window.theme.setText('Projeto de teste')
    project = window.project()
    project.images = ['cenas/original.png']
    project.statuses = {'imagens': 'CONCLUIDO'}
    window.repo.save(project)
    window.script.setPlainText('Roteiro revisado.')
    updated = window.project()
    restored = window.repo.get(updated.id)
    assert restored.images == ['cenas/original.png']
    assert restored.statuses == {'imagens': 'CONCLUIDO'}
    assert restored.script == 'Roteiro revisado.'
    window.theme.setText('')
    with pytest.raises(ValueError):
        window.project()
    assert window.repo.get(updated.id).theme == 'Projeto de teste'
    window.close()
    window.repo.cx.close()


def test_reopen_restores_cover_and_open_targets_project_cover(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window()
    window.theme.setText('Projeto com capa')
    project = window.project()
    source = tmp_path / 'origem.png'
    image = QImage(80, 120, QImage.Format.Format_RGB32)
    image.fill(0xff0044aa)
    assert image.save(str(source))
    cover = window.service.create_cover(project, source)

    window.refresh()
    window.load(window.items.item(0))
    assert window.cover_source.text() == str(source)
    assert window.cover_path.text() == str(cover)

    opened = []
    monkeypatch.setattr('app.ui.window.QDesktopServices.openUrl', lambda url: opened.append(url))
    window.open_cover()
    assert opened == [QUrl.fromLocalFile(str(cover))]
    window.close()
    window.repo.cx.close()


def test_accept_editorial_plan_persists_only_video_and_preserves_on_invalid_response(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window()
    window.theme.setText('Plano editorial')
    project = window.project()
    project.script = 'Roteiro local.'
    project.images = ['cenas/um.png', 'cenas/dois.png']
    project.audio_duration = 5
    project.statuses = {'voz': 'CONCLUIDO', 'legendas': 'CONCLUIDO', 'imagens': 'CONCLUIDO', 'video': 'CONCLUIDO'}
    window.current = window.repo.save(project)
    response = {'clips': [
        {'source': 'cenas/um.png', 'start': 0, 'end': 2, 'motion': 'slow_push', 'transition': 'cut'},
        {'source': 'cenas/dois.png', 'start': 2, 'end': 5, 'motion': 'slow_pull', 'transition': 'fade'},
    ]}
    request_hash = stable_input_hash(project.script, project.images, 5, project.settings.get('editing_preset'))
    window.accept_editorial_plan(project.id, response, 5, 'local-model', request_hash)
    saved = window.repo.get(project.id)
    assert saved.settings['timeline']['director']['model'] == 'local-model'
    assert saved.statuses == {'voz': 'CONCLUIDO', 'legendas': 'CONCLUIDO', 'imagens': 'CONCLUIDO', 'video': 'PENDENTE'}
    before_timeline, before_statuses = saved.settings['timeline'], dict(saved.statuses)
    with pytest.raises(ValueError):
        window.accept_editorial_plan(project.id, {'clips': [{'source': 'cenas/inventada.png'}]}, 5, 'local-model', request_hash)
    unchanged = window.repo.get(project.id)
    assert unchanged.settings['timeline'] == before_timeline and unchanged.statuses == before_statuses
    window.close(); window.repo.cx.close()


def test_accept_editorial_plan_rejects_changed_project_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    application = QApplication.instance() or QApplication([])
    window = Window(); window.theme.setText('Snapshot'); project = window.project()
    project.script, project.images, project.audio_duration = 'Antes.', ['cenas/um.png'], 2
    project.statuses = {'video': 'CONCLUIDO'}; project = window.repo.save(project)
    request_hash = stable_input_hash('Antes.', ['cenas/um.png'], 2, project.settings.get('editing_preset'))
    project.script = 'Depois.'; window.repo.save(project)
    response = {'clips': [{'source': 'cenas/um.png', 'start': 0, 'end': 2, 'motion': 'static_focus', 'transition': 'cut'}]}
    with pytest.raises(ValueError, match='mudou'):
        window.accept_editorial_plan(project.id, response, 2, 'local-model', request_hash)
    assert window.repo.get(project.id).settings.get('timeline') is None
    window.close(); window.repo.cx.close()
