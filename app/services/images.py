from dataclasses import dataclass, asdict
from pathlib import Path
import json, re

@dataclass
class ScenePlan:
    index: int; text: str; prompt: str

def plan_scenes(script: str, style: str = 'cinematográfico sombrio') -> list[ScenePlan]:
    chunks=[s.strip() for s in re.split(r'(?<=[.!?])\s+',script) if s.strip()]
    if not chunks: raise ValueError('Roteiro vazio.')
    return [ScenePlan(i, text, f'{style}, ilustração original sem personagens protegidos, representar: {text}') for i,text in enumerate(chunks,1)]

def save_scene_plan(script: str, target: Path, style: str = 'cinematográfico sombrio') -> list[ScenePlan]:
    scenes=plan_scenes(script,style); target.write_text(json.dumps([asdict(s) for s in scenes],ensure_ascii=False,indent=2),encoding='utf8'); return scenes
