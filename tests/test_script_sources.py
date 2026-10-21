import pytest
from app.services.script import generate_script

class Provider:
    def __init__(self):
        self.prompts = []
    def complete(self, prompt):
        self.prompts.append(prompt)
        return 'Rascunho para revisão.'

def test_factual_script_requires_notes_before_contacting_provider():
    provider = Provider()
    with pytest.raises(ValueError, match='fontes'):
        generate_script(provider, 'Carrie', 45)
    assert provider.prompts == []
    result = generate_script(provider, 'Carrie', 45, 'Fonte fornecida: notas verificadas.')
    assert result == 'Rascunho para revisão.'
    assert 'Fonte fornecida: notas verificadas.' in provider.prompts[0]
