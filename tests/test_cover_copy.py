import pytest

from app.services.cover_copy import fallback, prompt, response_schema, validate


def test_cover_copy_contract_is_short_and_reviewable():
    assert set(response_schema()['properties']) == {'headline', 'subheadline'}
    value = validate({'headline': 'AS TRÊS BRUXAS', 'subheadline': 'QUEM SÃO ELAS?'})
    assert value['headline'] == 'AS TRÊS BRUXAS'
    assert 'Roteiro' in prompt('Tema', 'Roteiro')


def test_cover_copy_rejects_unusable_or_unexpected_model_response():
    with pytest.raises(ValueError):
        validate({'headline': 'Curto', 'subheadline': 'Válido', 'extra': 'não'})
    assert fallback('Uma história').get('headline') == 'Uma história'
