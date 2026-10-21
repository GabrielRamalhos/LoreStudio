import json
import shutil
import subprocess
from pathlib import Path

class ProviderError(RuntimeError):
    pass

def executable(value):
    resolved = shutil.which(str(value)) if value else None
    if not resolved:
        raise FileNotFoundError(f'Executável não encontrado: {value or "não configurado"}')
    return resolved

def run_process(command, cancel=None, input_text=None, log=None, cwd=None):
    process = subprocess.Popen(command, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf8', errors='replace', cwd=cwd,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    first = True
    cancelled = False
    while True:
        if cancel is not None and cancel.is_set():
            process.kill()
            cancelled = True
        try:
            stdout, stderr = process.communicate(input=input_text if first else None, timeout=.2)
            break
        except subprocess.TimeoutExpired:
            first = False
    if log:
        Path(log).write_text(json.dumps({'command': command, 'returncode': process.returncode, 'stdout': stdout, 'stderr': stderr}, ensure_ascii=False, indent=2), encoding='utf8')
    if cancelled:
        raise ProviderError('Operação cancelada pelo usuário.')
    if process.returncode:
        raise ProviderError(f'Comando: {command!r}\nCódigo: {process.returncode}\n{stderr[-6000:] or stdout[-6000:]}')
    return stdout
