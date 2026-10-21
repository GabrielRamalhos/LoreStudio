import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from app.providers.comfyui import ComfyUIProvider
from app.services.settings import default_comfyui_workflow, resolve_comfyui_workflow


@pytest.fixture
def comfy_server():
    received = []
    image = QImage(320, 180, QImage.Format.Format_RGB32)
    image.fill(0xff222244)
    data = QBuffer()
    data.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(data, 'PNG')
    png = bytes(data.data())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, content_type='application/json'):
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, body))
            self.reply({'prompt_id': 'job-1'} if self.path == '/prompt' else {})

        def do_GET(self):
            received.append((self.path, None))
            if self.path.startswith('/history/'):
                self.reply({'job-1': {'outputs': {'9': {'images': [{'filename': 'scene.png'}]}}}})
            else:
                self.reply(png, 'image/png')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', received, png
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_comfyui_enqueues_local_workflow_and_downloads_only_returned_image(comfy_server):
    url, received, png = comfy_server
    provider = ComfyUIProvider(url, json.dumps({'1': {'inputs': {'text': 'old', 'ckpt_name': 'old.safetensors'}}}), 'chosen.safetensors')
    assert provider.generate('uma cena') == png
    prompt = received[0][1]['prompt']['1']['inputs']
    assert prompt == {'text': 'uma cena', 'ckpt_name': 'chosen.safetensors'}
    assert any(path.startswith('/view?filename=scene.png') for path, _ in received)


def test_comfyui_rejects_remote_or_missing_workflow():
    with pytest.raises(ValueError):
        ComfyUIProvider('https://example.com', '{}')
    with pytest.raises(ValueError):
        ComfyUIProvider('http://127.0.0.1:8188', '')


def test_comfyui_reports_how_to_start_an_unavailable_local_server():
    provider = ComfyUIProvider('http://127.0.0.1:1', '{}')
    with pytest.raises(RuntimeError, match='Inicie o servidor em http://127.0.0.1:8188'):
        provider.generate('uma cena')


def test_bundled_conservative_profile_is_used_only_when_project_has_no_choice():
    default = json.loads(default_comfyui_workflow())
    assert default['4']['inputs'] == {'width': 512, 'height': 896, 'batch_size': 1}
    assert default['5']['inputs']['steps'] == 20
    assert resolve_comfyui_workflow({}) == default_comfyui_workflow()
    assert resolve_comfyui_workflow({'comfyui_workflow': '{"custom": true}'}) == '{"custom": true}'
