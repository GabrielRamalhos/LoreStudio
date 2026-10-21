import re
from app.core.models import Project
from app.database.repository import ProjectRepository

def create_batch(repo: ProjectRepository, topics: list[str], category: str = 'Filmes', duration: int = 45) -> list[Project]:
    created=[]
    for topic in topics:
        if topic.strip(): created.append(repo.save(Project(None,'',topic.strip(),category,'Short',duration)))
    return created

def derive_shorts(long_script: str, max_chars: int = 420) -> list[str]:
    sentences=[x.strip() for x in re.split(r'(?<=[.!?])\s+',long_script) if x.strip()]
    if not sentences: raise ValueError('Roteiro vazio.')
    groups=[]; current=[]; length=0
    for sentence in sentences:
        if current and length+len(sentence)+1 > max_chars: groups.append(' '.join(current)); current=[]; length=0
        current.append(sentence); length += len(sentence)+1
    if current: groups.append(' '.join(current))
    return groups
