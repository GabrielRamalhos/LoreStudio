from pathlib import Path

import pytest

from app.services.visual_director import describe_imported_images, editorial_context


class LocalVisionStub:
    def __init__(self):
        self.calls = []

    def complete_image_structured(self, prompt, schema, image):
        self.calls.append((prompt, schema, image))
        return {'description': 'Pessoa em uma cidade escura.', 'subjects': ['pessoa', 'cidade'], 'mood': 'sombrio'}


def test_visual_notes_keep_project_sources_in_import_order(tmp_path):
    (tmp_path / 'cenas').mkdir()
    (tmp_path / 'cenas/um.png').write_bytes(b'image-one')
    (tmp_path / 'cenas/dois.png').write_bytes(b'image-two')
    provider = LocalVisionStub()
    notes = describe_imported_images(provider, tmp_path, ['cenas/um.png', 'cenas/dois.png'])
    assert [note['source'] for note in notes] == ['cenas/um.png', 'cenas/dois.png']
    assert len(provider.calls) == 2
    context = editorial_context(notes)
    assert 'cenas/um.png' in context and 'Pessoa em uma cidade escura.' in context


def test_visual_notes_reject_missing_or_unsafe_project_media(tmp_path):
    provider = LocalVisionStub()
    with pytest.raises(FileNotFoundError):
        describe_imported_images(provider, tmp_path, ['cenas/ausente.png'])
    outside = tmp_path.parent / 'outside.png'
    outside.write_bytes(b'outside')
    with pytest.raises(FileNotFoundError):
        describe_imported_images(provider, tmp_path, ['../outside.png'])
