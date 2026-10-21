"""Optional, local-only image descriptions for editorial planning.

This service never invents or modifies media. It turns a model response into
compact, auditable visible-element notes that the editorial director may use
to assign the user-owned images to matching narration segments.
"""
from __future__ import annotations

from pathlib import Path

VISION_DIRECTOR_VERSION = 'visual-director/v1'


def response_schema() -> dict:
    return {
        'type': 'object', 'additionalProperties': False,
        'required': ['description', 'subjects', 'mood'],
        'properties': {
            'description': {'type': 'string'},
            'subjects': {'type': 'array', 'items': {'type': 'string'}},
            'mood': {'type': 'string'},
        },
    }


def prompt() -> str:
    return (
        'Describe only visible elements in this image for a local video editor. '
        'Do not identify a real person or claim franchise facts that are not visible. '
        'Return JSON only: a concise Portuguese description (max 180 characters), '
        'up to five visible subjects, and a concise visual mood.'
    )


def _clean_note(source: str, response: dict) -> dict:
    description = str(response.get('description', '')).strip()
    mood = str(response.get('mood', '')).strip()
    subjects = response.get('subjects')
    if (not description or len(description) > 180 or not mood or len(mood) > 80
            or not isinstance(subjects, list) or len(subjects) > 5
            or not all(isinstance(value, str) and value.strip() and len(value.strip()) <= 60 for value in subjects)):
        raise ValueError('O modelo visual retornou uma descrição fora do formato permitido.')
    return {'source': source, 'description': description, 'subjects': [value.strip() for value in subjects], 'mood': mood}


def describe_imported_images(provider, root: Path, sources: list[str]) -> list[dict]:
    """Describe each project-owned file in import order using a local model."""
    root = Path(root).resolve()
    notes = []
    for source in sources:
        path = (root / source).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FileNotFoundError('Imagem do projeto indisponível para análise visual.')
        notes.append(_clean_note(source, provider.complete_image_structured(prompt(), response_schema(), path.read_bytes())))
    return notes


def editorial_context(notes: list[dict]) -> str:
    """Serialize vetted visual notes for the text-only editorial director."""
    if not notes:
        return ''
    rows = [
        f"- {note['source']}: visível={note['description']}; sujeitos={', '.join(note['subjects'])}; clima={note['mood']}"
        for note in notes
    ]
    return '\n'.join(rows)
