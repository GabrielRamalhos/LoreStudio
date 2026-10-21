import pytest

from app.services.cover_plan import fallback, prompt, response_schema, validate


def test_cover_plan_is_strictly_grounded_in_project_images():
    sources = ['cenas/a.png', 'cenas/b.jfif']
    plan = validate({
        'source': 'cenas/b.jfif', 'headline': 'O SEGREDO REVELADO',
        'subheadline': 'VOCÊ PERCEBEU ISSO?', 'highlight_words': ['SEGREDO'], 'preset': 'revelacao',
        'text_position': 'top', 'effect_intensity': 'moderada',
        'reason': 'A imagem mostra o elemento central do roteiro.',
    }, sources)
    assert plan['source'] == 'cenas/b.jfif'
    assert plan['highlight_words'] == ['SEGREDO']
    assert set(response_schema()['required']) == set(plan)
    assert 'visual_notes' in prompt('Tema', 'Roteiro', sources, [])


def test_cover_plan_rejects_unowned_source_or_unusable_copy():
    sources = ['cenas/a.png']
    valid = fallback('Tema curto', sources)
    assert valid['source'] == 'cenas/a.png'
    with pytest.raises(ValueError, match='não pertence'):
        validate({**valid, 'source': 'fora.png'}, sources)
    with pytest.raises(ValueError, match='headline'):
        validate({**valid, 'headline': 'x'}, sources)
    with pytest.raises(ValueError, match='headline'):
        validate({**valid, 'headline': 'UM TÍTULO LONGO DEMAIS PARA ESTA CAPA'}, sources)
    with pytest.raises(ValueError, match='destaque'):
        validate({**valid, 'highlight_words': ['FORA']}, sources)
