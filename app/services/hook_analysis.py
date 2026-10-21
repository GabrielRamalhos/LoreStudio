"""Local, non-destructive review of the opening hook of a user script."""
from __future__ import annotations

import re


ANGLES = ('conflito', 'consequência', 'curiosidade')


def opening_excerpt(script: str, limit: int = 340) -> str:
    """Return the opening spoken unit without estimating audience retention."""
    clean = ' '.join(str(script).split())
    if not clean:
        raise ValueError('Escreva um roteiro antes de analisar o gancho.')
    sentences = re.split(r'(?<=[.!?])\s+', clean)
    excerpt = ' '.join(sentences[:2]).strip()
    return excerpt[:limit].rstrip()


def response_schema() -> dict:
    suggestion = {
        'type': 'object', 'additionalProperties': False,
        'required': ['text', 'angle', 'reason'],
        'properties': {
            'text': {'type': 'string'},
            'angle': {'type': 'string', 'enum': list(ANGLES)},
            'reason': {'type': 'string'},
        },
    }
    return {'type': 'object', 'additionalProperties': False, 'required': ['suggestions'],
            'properties': {'suggestions': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'items': suggestion}}}


def prompt(script: str) -> str:
    excerpt = opening_excerpt(script)
    return (
        'Você revisa apenas o gancho de um roteiro em português brasileiro. Retorne somente JSON no schema recebido. '
        'Analise os primeiros segundos como texto, sem prever retenção ou desempenho. Sugira no máximo três alternativas '
        'curtas, uma por ângulo: conflito, consequência e curiosidade. Cada alternativa deve reorganizar ou destacar '
        'somente fatos já presentes no trecho ou roteiro; não invente personagens, acontecimentos, datas, poderes, '
        'referências ou promessas. Não altere o roteiro original. O usuário deve revisar qualquer sugestão antes de usar.\n'
        f'Trecho inicial:\n{excerpt}\n\nRoteiro completo:\n{script.strip()}'
    )


def validate_response(response: dict, script: str) -> list[dict]:
    if not isinstance(response, dict) or set(response) != {'suggestions'} or not isinstance(response['suggestions'], list):
        raise ValueError('O analisador de gancho retornou um formato inválido.')
    source = ' '.join(str(script).split()).casefold()
    suggestions, used = [], set()
    for item in response['suggestions']:
        if not isinstance(item, dict) or set(item) != {'text', 'angle', 'reason'}:
            raise ValueError('O analisador de gancho retornou campos inválidos.')
        text, angle, reason = (item['text'].strip(), item['angle'], item['reason'].strip())
        if not text or len(text) > 240 or angle not in ANGLES or not reason or len(reason) > 280:
            raise ValueError('Uma sugestão de gancho é inválida.')
        # A capitalized entity absent from the source is strong evidence that
        # the local model invented a person/place/title. Reject the whole
        # response rather than presenting an unsafe suggestion.
        invented = [token for token in re.findall(r'\b[A-ZÀ-Ý][\wÀ-ÿ-]{2,}\b', text)
                    if token.casefold() not in source and token.casefold() not in {'o', 'a', 'e', 'em'}]
        if invented:
            raise ValueError('A sugestão de gancho introduziu referências que não constam no roteiro.')
        key = text.casefold()
        if key in used:
            continue
        used.add(key)
        suggestions.append({'text': text, 'angle': angle, 'reason': reason})
    if not suggestions:
        raise ValueError('O analisador não retornou sugestões aproveitáveis.')
    return suggestions
