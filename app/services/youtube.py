from app.providers.llm import LLMProvider
def metadata(provider: LLMProvider, script: str) -> str:
    return provider.complete('Gere JSON com 3 títulos, descrição, hashtags, tags e ideia de thumbnail para o roteiro:\n'+script)
