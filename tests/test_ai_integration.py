import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.providers.llm import OllamaProvider
from app.services.ai import generate_pack, validate_pack
from app.services.script import generate_script


def pack():
    return dict(scenes=[dict(text='História fictícia.', prompt='Floresta escura original.')],
                titles=['Um', 'Dois', 'Três'], description='Uma história fictícia.', hashtags=['#ficção'],
                tags=['ficção'], thumbnail_prompt='Uma floresta.', cta='Inscreva-se.')


@pytest.fixture
def ollama_server():
    requests = []
    started, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            requests.append(self.path)
            self.reply({'models': [{'name': 'local:latest'}]})
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(payload)
            if payload['model'] == 'slow':
                self.send_response(200)
                self.send_header('Content-Length', '100')
                self.end_headers()
                self.wfile.flush()
                started.set()
                release.wait(5)
            elif payload['model'] == 'missing':
                self.reply({'error': 'model not found'}, 404)
            elif payload['model'] == 'invalid':
                self.reply({'done': True, 'response': '{invalid'})
            else:
                self.reply({'done': True, 'response': json.dumps(pack()) if payload.get('format') else 'História fictícia.'})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests, started, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join()


def test_local_http_models_script_and_structured_pack(ollama_server):
    url, requests, _, _ = ollama_server
    provider = OllamaProvider('local:latest', url)
    assert provider.list_models() == ['local:latest']
    assert generate_script(provider, 'Floresta', 45, fiction=True) == 'História fictícia.'
    assert generate_pack(provider, 'História fictícia.', 1, 'sombrio') == validate_pack(pack(), 'História fictícia.')
    assert requests[0] == '/api/tags'
    assert requests[-1]['format'] == 'json'
    assert requests[-1]['stream'] is False


def test_provider_errors_and_invalid_json(ollama_server):
    url, _, _, _ = ollama_server
    with pytest.raises(RuntimeError, match='Modelo não encontrado'):
        OllamaProvider('missing', url).complete('tema')
    with pytest.raises(ValueError, match='JSON válido'):
        OllamaProvider('invalid', url).complete_json('tema')
    with pytest.raises(ValueError, match='local'):
        OllamaProvider('model', 'https://example.com')


def test_provider_sends_image_bytes_only_to_local_ollama(ollama_server):
    url, requests, _, _ = ollama_server
    provider = OllamaProvider('local:latest', url)
    result = provider.complete_image_structured('Descreva.', {'type': 'object'}, b'local-image')
    assert result == pack()
    payload = requests[-1]
    assert base64.b64decode(payload['images'][0]) == b'local-image'
    with pytest.raises(ValueError, match='12 MB'):
        provider.complete_image_structured('Descreva.', {'type': 'object'}, b'x' * (12 * 1024 * 1024 + 1))


def test_cancel_interrupts_active_http_read(ollama_server):
    url, _, started, release = ollama_server
    provider = OllamaProvider('slow', url)
    provider.cancel = threading.Event()
    errors = []
    def request():
        try:
            provider.complete('tema')
        except RuntimeError as error:
            errors.append(str(error))
    worker = threading.Thread(target=request, daemon=True)
    worker.start()
    try:
        assert started.wait(2), 'Servidor não recebeu a requisição.'
        provider.cancel.set()
        worker.join(2)
        assert not worker.is_alive()
        assert errors and 'cancelada' in errors[0]
    finally:
        release.set()


def test_pack_rejects_changed_narration_and_wrong_count():
    with pytest.raises(ValueError, match='roteiro inteiro'):
        validate_pack(pack(), 'Outro roteiro.')
    with pytest.raises(ValueError, match='quantidade'):
        validate_pack(pack(), 'História fictícia.', 2)


def test_ai_panel_persists_edits_and_marks_stale(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.chdir(tmp_path)
    from PySide6.QtWidgets import QApplication
    from app.ui.window import Window
    application = QApplication.instance() or QApplication([])
    window = Window()
    try:
        window.theme.setText('Floresta')
        window.script.setPlainText('História fictícia.')
        window.fiction.setChecked(True)
        window.accept_plan(pack(), window.script.toPlainText())
        window.ai.titles[0].setText('Título editado')
        project_id = window.project().id
        window.new_project()
        window.load(window.items.item(0))
        assert window.current.id == project_id
        assert window.ai.titles[0].text() == 'Título editado'
        assert window.fiction.isChecked()
        window.script.setPlainText('Roteiro novo.')
        assert 'precisa ser atualizado' in window.ai.notice.text()
        assert window.ai.scenes.item(0, 0).text() == 'História fictícia.'
        window.script.setPlainText('História fictícia.')
        window.ai.titles[0].clear()
        assert 'incompleto' in window.ai.notice.text()
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Cancel)
        window.new_project()
        assert window.current.id == project_id
        window.ollama_status.setText('Conectado')
        window.ollama_url.setText('http://localhost:12345')
        assert 'Verifique novamente' in window.ollama_status.text()
        monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Discard)
    finally:
        window.close()
        window.repo.cx.close()
