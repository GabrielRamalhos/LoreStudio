"""Local, explicit feedback used as optional editorial context for Ollama."""
from datetime import datetime, timezone


def save_outcome(repo, project_id: int, rating: str, feedback: str) -> None:
    if not feedback.strip():
        raise ValueError('Descreva o resultado ou aprendizado antes de salvar.')
    repo.save_outcome(project_id, rating, feedback, datetime.now(timezone.utc).isoformat())


def planning_context(repo, limit: int = 8) -> str:
    outcomes = repo.planning_outcomes(limit)
    if not outcomes:
        return ''
    lines = ['Aprendizados locais de projetos anteriores (contexto editorial, não são fatos para o roteiro):']
    for item in outcomes:
        lines.append(f"- [{item['rating']}] {item['category']}/{item['format']}, tema: {item['theme']}. Feedback: {item['feedback']}")
    return '\n'.join(lines)
