from pathlib import Path
import json

from app.services.diagnostics import optional_component_diagnostics
from app.services.paths import UserPaths, migrate_legacy_data, user_paths
from app.services.product_info import export_sanitized_diagnostics, sanitized_diagnostics
from app.services.settings import Settings, SettingsStore, resolve_local_tools


def test_user_paths_use_localappdata_and_never_the_installation(monkeypatch, tmp_path):
    monkeypatch.delenv('LORE_OCULTA_DATA_DIR', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local-app-data'))
    paths = user_paths()
    assert paths.root == tmp_path / 'local-app-data' / 'Gabriel Ramalhos' / 'Lore Oculta Studio'
    assert paths.projects.parent == paths.root


def test_legacy_migration_copies_without_overwriting_existing_user_data(tmp_path):
    legacy = tmp_path / 'legacy'
    (legacy / 'config').mkdir(parents=True)
    (legacy / 'config' / 'settings.json').write_text('{"output_dir": "projects"}', encoding='utf8')
    (legacy / 'projects' / 'old').mkdir(parents=True)
    (legacy / 'projects' / 'old' / 'roteiro.txt').write_text('original', encoding='utf8')
    root = tmp_path / 'data'
    paths = UserPaths(root, root / 'config' / 'settings.json', root / 'lore_oculta.db', root / 'projects', root / 'logs')
    migrate_legacy_data(paths, legacy)
    (paths.projects / 'old' / 'roteiro.txt').write_text('user version', encoding='utf8')
    migrate_legacy_data(paths, legacy)
    assert paths.settings.is_file()
    assert (paths.projects / 'old' / 'roteiro.txt').read_text(encoding='utf8') == 'user version'
    assert (legacy / 'projects' / 'old' / 'roteiro.txt').read_text(encoding='utf8') == 'original'


def test_optional_integrations_are_explicit_when_absent():
    message = '\n'.join(optional_component_diagnostics(Settings()))
    assert 'Ollama: opcional' in message
    assert 'ComfyUI: opcional' in message
    assert 'Piper: opcional' in message


def test_release_uses_audited_bundled_ffmpeg_before_development_fallback(tmp_path):
    executable = tmp_path / 'vendor' / 'ffmpeg' / 'ffmpeg.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'approved-release-placeholder')
    settings = resolve_local_tools(Settings(ffmpeg='missing-ffmpeg'), tmp_path)
    assert settings.ffmpeg == str(executable)


def test_sanitized_diagnostics_never_serializes_project_or_credential_values(tmp_path, monkeypatch):
    monkeypatch.setenv('LORE_OCULTA_DATA_DIR', str(tmp_path / 'local-data'))
    settings = Settings(ollama_url='http://token:secret@example.test', piper_model='private-voice.onnx')
    report = sanitized_diagnostics(settings)
    exported = export_sanitized_diagnostics(tmp_path / 'diagnostics.json', settings)
    contents = exported.read_text(encoding='utf8')
    assert report['product']['version']
    assert 'token:secret' not in contents
    assert 'private-voice.onnx' not in contents
    assert 'projects' not in report
    assert json.loads(contents)['platform']['system']


def test_component_manifest_declares_audited_paths_and_no_final_hashes(tmp_path):
    manifest = json.loads((Path(__file__).parents[1] / 'packaging' / 'COMPONENT_MANIFEST.json').read_text(encoding='utf8'))
    assert manifest['version']
    for component in manifest['components']:
        assert component['path']
        assert component['license']
        assert component['origin']
        assert component['sha256'] == 'generated-at-release'


def test_onboarding_completion_persists_without_changing_existing_settings(tmp_path):
    store = SettingsStore(tmp_path / 'config' / 'settings.json')
    settings = Settings(ollama_model='custom-model', onboarding_complete=True)
    store.save(settings)
    loaded = store.load()
    assert loaded.onboarding_complete is True
    assert loaded.ollama_model == 'custom-model'
