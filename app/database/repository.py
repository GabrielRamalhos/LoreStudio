import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from app.core.models import Project, validate

def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "projeto"

class ProjectRepository:
    def __init__(self, db: Path, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cx = sqlite3.connect(db, check_same_thread=False)
        self.cx.row_factory = sqlite3.Row
        self.cx.execute("CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, slug TEXT UNIQUE, theme TEXT, category TEXT, format TEXT, duration INTEGER, script TEXT NOT NULL DEFAULT '', images TEXT NOT NULL DEFAULT '[]', statuses TEXT NOT NULL DEFAULT '{}')")
        columns = {r[1] for r in self.cx.execute('PRAGMA table_info(projects)')}
        for name, definition in [('settings', "TEXT NOT NULL DEFAULT '{}'"), ('audio_duration', 'REAL'), ('history', "TEXT NOT NULL DEFAULT '[]'")]:
            if name not in columns:
                self.cx.execute(f'ALTER TABLE projects ADD COLUMN {name} {definition}')
        self.cx.execute("CREATE TABLE IF NOT EXISTS project_outcomes (project_id INTEGER PRIMARY KEY, rating TEXT NOT NULL, feedback TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id))")
        self.cx.commit()

    def save(self, p: Project) -> Project:
        validate(p.theme, p.duration, p.format)
        if not p.category.strip():
            raise ValueError('Categoria é obrigatória.')
        with self.lock:
            if p.id is None:
                slug = slugify(p.slug or p.theme)
                if (self.root / slug).exists() or self.cx.execute('SELECT 1 FROM projects WHERE slug=?', (slug,)).fetchone():
                    slug += '-' + uuid.uuid4().hex[:8]
                p.slug = slug
            elif not self.cx.execute('SELECT 1 FROM projects WHERE id=? AND slug=?', (p.id, p.slug)).fetchone():
                raise ValueError('Projeto não encontrado ou diretório alterado.')
            directory = self.root / p.slug
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'cenas').mkdir(exist_ok=True)
            temporary = directory / 'roteiro.tmp'
            temporary.write_text(p.script, encoding='utf8')
            temporary.replace(directory / 'roteiro.txt')
            fields = (p.theme, p.category, p.format, p.duration, p.script, json.dumps(p.images), json.dumps(p.statuses), json.dumps(p.settings), p.audio_duration, json.dumps(p.history, ensure_ascii=False))
            with self.cx:
                if p.id is None:
                    p.id = self.cx.execute('INSERT INTO projects(theme,category,format,duration,script,images,statuses,settings,audio_duration,history,slug) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (*fields, p.slug)).lastrowid
                else:
                    self.cx.execute('UPDATE projects SET theme=?,category=?,format=?,duration=?,script=?,images=?,statuses=?,settings=?,audio_duration=?,history=? WHERE id=?', (*fields, p.id))
            return p

    def get(self, id: int) -> Project:
        with self.lock:
            row = self.cx.execute('SELECT * FROM projects WHERE id=?', (id,)).fetchone()
        if row is None:
            raise KeyError(id)
        values = dict(row)
        for key in ('images', 'statuses', 'settings', 'history'):
            values[key] = json.loads(values[key])
        return Project(**values)

    def list(self):
        with self.lock:
            ids = [r[0] for r in self.cx.execute('SELECT id FROM projects ORDER BY id DESC')]
            return [self.get(id) for id in ids]

    def save_outcome(self, project_id: int, rating: str, feedback: str, updated_at: str) -> None:
        if rating not in {'positive', 'neutral', 'negative'}:
            raise ValueError('Avaliação inválida.')
        if not self.cx.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
            raise KeyError(project_id)
        with self.lock, self.cx:
            self.cx.execute('INSERT INTO project_outcomes(project_id,rating,feedback,updated_at) VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET rating=excluded.rating, feedback=excluded.feedback, updated_at=excluded.updated_at', (project_id, rating, feedback.strip(), updated_at))

    def planning_outcomes(self, limit: int = 8) -> "list[dict]":
        with self.lock:
            rows = self.cx.execute('SELECT p.theme, p.category, p.format, o.rating, o.feedback, o.updated_at FROM project_outcomes o JOIN projects p ON p.id=o.project_id WHERE trim(o.feedback) <> \'\' ORDER BY o.updated_at DESC LIMIT ?', (limit,)).fetchall()
        return [dict(row) for row in rows]

    def outcome(self, project_id: int) -> dict | None:
        with self.lock:
            row = self.cx.execute('SELECT rating, feedback, updated_at FROM project_outcomes WHERE project_id=?', (project_id,)).fetchone()
        return dict(row) if row else None
