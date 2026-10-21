from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from app.core.models import StepStatus

def generate_scenes(projects, project, scenes, provider, cancel):
    root=projects.artifact_dir(project); folder=root/'cenas'; folder.mkdir(exist_ok=True); created=[]
    try:
        for scene in scenes:
            if cancel.is_set(): raise RuntimeError('Operação cancelada pelo usuário.')
            image=QImage()
            if not image.loadFromData(provider.generate(scene['prompt'],cancel)): raise ValueError(f'Cena {scene["index"]}: imagem inválida retornada pelo ComfyUI.')
            scaled=image.scaled(1080, 1920, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x=max(0,(scaled.width()-1080)//2); y=max(0,(scaled.height()-1920)//2)
            output=folder/f'gerada-{scene["index"]:02d}.png'
            if not scaled.copy(x,y,1080,1920).save(str(output),'PNG'): raise OSError('Não foi possível salvar imagem gerada.')
            created.append(output.relative_to(root).as_posix()); project.images=list(created); projects.repo.save(project)
        projects.set_status(project,'imagens',StepStatus.DONE,f'{len(created)} imagem(ns) gerada(s).'); return created
    except Exception as error: projects.set_status(project,'imagens',StepStatus.ERROR,str(error)); raise
