import pytest
from app.services.ai import generate_pack


def valid_pack():
    return {'scenes': [{'text': 'Primeira frase.', 'prompt': 'Floresta.'},
                       {'text': 'Segunda frase.', 'prompt': 'Montanha.'}],
            'titles': ['Um', 'Dois', 'Três'], 'description': 'Descrição.',
            'hashtags': ['#história'], 'tags': ['história'], 'thumbnail_prompt': 'Paisagem.', 'cta': 'Inscreva-se.'}


class Provider:
    def __init__(self, result):
        self.result = result
        self.prompt = None
    def complete_json(self, prompt):
        self.prompt = prompt
        return self.result


def test_legacy_example_envelope_is_validated_and_unwrapped():
    provider = Provider({'example': valid_pack(), 'visual_style': 'sombrio'})
    result = generate_pack(provider, 'Primeira frase. Segunda frase.', 2, 'sombrio')
    assert len(result['scenes']) == 2
    assert ' '.join(scene['text'] for scene in result['scenes']) == 'Primeira frase. Segunda frase.'
    assert '"example":' not in provider.prompt
    assert 'FORMATO DE SAÍDA' in provider.prompt
    assert 'DADOS DE ENTRADA' in provider.prompt


def test_wrapped_wrong_count_and_changed_script_remain_rejected():
    provider = Provider({'example': valid_pack()})
    with pytest.raises(ValueError, match='quantidade'):
        generate_pack(provider, 'Primeira frase. Segunda frase.', 8, 'sombrio')
    with pytest.raises(ValueError, match='roteiro inteiro'):
        generate_pack(provider, 'Roteiro diferente.', 2, 'sombrio')


def test_missing_scenes_and_ambiguous_wrapper_remain_rejected():
    for result in ({'titles': ['Um']}, {'example': valid_pack(), 'result': valid_pack()}):
        with pytest.raises(ValueError, match='modelo não retornou'):
            generate_pack(Provider(result), 'Primeira frase. Segunda frase.', 2, 'sombrio')


def test_eight_scenes_retry_preserves_script_and_enforces_schema():
    expected = valid_pack()
    expected['scenes'] = [{'text': f'Frase {i}.', 'prompt': f'Paisagem {i}.'} for i in range(8)]
    script = ' '.join(scene['text'] for scene in expected['scenes'])
    class RepairProvider(Provider):
        calls = 0
        def complete_structured(self, prompt, schema):
            self.calls += 1
            assert schema['properties']['scenes']['minItems'] == 8
            assert schema['properties']['scenes']['maxItems'] == 8
            assert schema['properties']['titles']['minItems'] == 3
            assert script in prompt
            assert 'exatamente 8 cenas' in prompt
            assert 'estilo de teste' in prompt
            return expected
    provider = RepairProvider({'example': valid_pack()})
    result = generate_pack(provider, script, 8, 'estilo de teste')
    assert provider.calls == 1
    assert len(result['scenes']) == 8
    assert ' '.join(scene['text'] for scene in result['scenes']) == script


@pytest.mark.parametrize('field', ['prompt', 'titles', 'description'])
def test_wrapped_missing_fields_are_never_accepted(field):
    invalid = valid_pack()
    if field == 'prompt':
        del invalid['scenes'][0]['prompt']
    else:
        del invalid[field]
    with pytest.raises(ValueError):
        generate_pack(Provider({'example': invalid}), 'Primeira frase. Segunda frase.', 2, 'sombrio')


def test_repair_attempt_is_bounded_and_still_validated():
    class InvalidRepair(Provider):
        calls = 0
        def complete_structured(self, prompt, schema):
            self.calls += 1
            return valid_pack()
    provider = InvalidRepair({})
    with pytest.raises(ValueError, match='roteiro inteiro'):
        generate_pack(provider, 'Outra narração inteira.', 2, 'sombrio')
    assert provider.calls == 1


def test_provider_sends_schema_without_changing_legacy_json(monkeypatch):
    import json
    from app.providers.llm import OllamaProvider
    provider = OllamaProvider('local')
    payloads = []
    def request(path, payload):
        payloads.append(payload)
        return {'done': True, 'response': json.dumps(valid_pack())}
    monkeypatch.setattr(provider, '_request', request)
    schema = {'type': 'object', 'required': ['scenes']}
    assert provider.complete_structured('roteiro', schema) == valid_pack()
    assert payloads[0]['format'] == schema
    assert payloads[0]['options']['temperature'] == 0
    assert provider.complete_json('roteiro') == valid_pack()
    assert payloads[1]['format'] == 'json'


def test_visual_provider_allows_enough_tokens_to_complete_structured_description(monkeypatch):
    import json
    from app.providers.llm import OllamaProvider
    provider = OllamaProvider('local')
    payloads = []
    monkeypatch.setattr(provider, '_request', lambda _path, payload: payloads.append(payload) or {
        'done': True, 'response': json.dumps({'description': 'Pessoa.', 'subjects': [], 'mood': 'calmo'}),
    })
    schema = {'type': 'object', 'properties': {'description': {'type': 'string'}}}
    assert provider.complete_image_structured('imagem', schema, b'bytes')['description'] == 'Pessoa.'
    assert payloads[0]['options'] == {'temperature': 0, 'num_predict': 256}


def test_repair_wrong_count_is_rejected_even_with_schema():
    class WrongCount(Provider):
        calls = 0
        def complete_structured(self, prompt, schema):
            self.calls += 1
            return valid_pack()
    provider = WrongCount({})
    with pytest.raises(ValueError, match='quantidade'):
        generate_pack(provider, 'Uma primeira frase completa. Uma segunda frase também completa.', 8, 'sombrio')
    assert provider.calls == 1


@pytest.mark.parametrize('count', [1, 3, 8])
def test_prompt_only_repair_keeps_every_original_word(count):
    script = '  Uma história original.\n\nOutra cena mantém “aspas”, ação e acentos! A conclusão fica intacta.  '
    class PromptOnly(Provider):
        def complete_structured(self, prompt, schema):
            assert schema['properties']['scenes']['items']['required'] == ['prompt']
            response = valid_pack()
            response['scenes'] = [{'prompt': f'Cena visual {index}.'} for index in range(count)]
            return response
    result = generate_pack(PromptOnly({}), script, count, 'sombrio')
    assert len(result['scenes']) == count
    assert ' '.join(' '.join(scene['text'] for scene in result['scenes']).split()) == ' '.join(script.split())
    assert all(scene['text'].strip() for scene in result['scenes'])
    assert [scene['index'] for scene in result['scenes']] == list(range(1, count+1))
