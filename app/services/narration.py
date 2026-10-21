import html
import re
import unicodedata


def normalize_text(text: str, pronunciations: dict[str, str] | None = None) -> str:
    """Converte roteiro editorial em fala limpa, sem alterar palavras comuns."""
    value = html.unescape(text or '')
    value = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', value)
    value = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', value)
    value = re.sub(r'\[([^\]]+)\]\[[^\]]*\]', r'\1', value)
    value = re.sub(r'```[^\r\n]*\r?\n(.*?)```', r'\1', value, flags=re.S)
    value = value.replace('```', '')
    value = re.sub(r'`([^`]*)`', r'\1', value)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = re.sub(r'https?://\S+|www\.\S+', ' ', value, flags=re.I)
    value = re.sub(r'(?m)^\s{0,3}(?:#{1,6}|>|[-*+] |\d+[.)] )\s*', '', value)
    value = re.sub(r'(?<!\w)[*_~]+|[*_~]+(?!\w)', '', value)
    value = ''.join(' ' if unicodedata.category(char).startswith('C') else char for char in value)
    value = value.replace('&', ' e ')
    for written, spoken in sorted((pronunciations or {}).items(), key=lambda item: len(item[0]), reverse=True):
        if written.strip() and spoken.strip():
            value = re.sub(r'(?<!\w)' + re.escape(written) + r'(?!\w)', spoken, value, flags=re.I)
    value = re.sub(r'\s+', ' ', value).strip()
    if not value:
        raise ValueError('O roteiro não contém texto narrável após a normalização.')
    return value

def speech_text(text: str) -> str:
    """Ajustes mínimos exclusivos para fala; nunca altera roteiro ou legendas."""
    replacements = {'Hal Jordan': 'Rau Djórdan', 'Hal': 'Rau', 'Wayne': 'Uayne', 'Superman': 'Supermen', 'DC': 'Dê Cê'}
    # Kokoro can drop the initial vowel of this proper name in Portuguese.
    # This is a speech-only phonetic hint; stored scripts and captions stay
    # exactly as the user wrote them.
    replacements['Ultron'] = 'Últron'
    value = text
    for written, spoken in replacements.items():
        value = re.sub(r'(?<!\w)' + re.escape(written) + r'(?!\w)', spoken, value, flags=re.I)
    return value
