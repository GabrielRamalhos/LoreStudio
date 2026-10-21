from abc import ABC, abstractmethod
import base64
import ipaddress
import http.client
import json
import socket
import threading
import time
import urllib.parse

class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str) -> str: ...

class OllamaProvider(LLMProvider):
    def __init__(self, model: str, endpoint: str = 'http://localhost:11434/api/generate'):
        self.model = model.strip()
        parsed = urllib.parse.urlsplit(endpoint.strip())
        host = parsed.hostname or ''
        try:
            local = host == 'localhost' or ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if parsed.scheme != 'http' or not local or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Use o endereço HTTP do Ollama local, por exemplo http://localhost:11434.')
        if parsed.path.rstrip('/') not in ('', '/api/generate'):
            raise ValueError('Informe a raiz do servidor Ollama, sem caminho adicional.')
        self.base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
        self.endpoint = self.base_url + '/api/generate'
        self.cancel = None

    def _request(self, path, payload=None, timeout=90):
        data = json.dumps(payload).encode('utf8') if payload is not None else None
        parsed = urllib.parse.urlsplit(self.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=min(5, timeout))
        finished, aborted = threading.Event(), threading.Event()
        outcome = {}
        # Conexão direta: sem proxy/redirecionamentos. Guardamos o socket mesmo
        # quando HTTP/1.0 o desassocia da conexão, para interromper leitura ativa.
        def request():
            try:
                connection.connect()
                wire = connection.sock
                outcome['socket'] = wire
                wire.settimeout(timeout)
                if aborted.is_set():
                    return
                connection.request('POST' if data is not None else 'GET', path, body=data, headers={'Content-Type': 'application/json'})
                with connection.getresponse() as response:
                    if response.status != 200:
                        detail = 'Modelo não encontrado; confira os modelos instalados.' if response.status == 404 and payload else 'Confira o servidor e o modelo selecionado.'
                        raise RuntimeError(f'Ollama retornou HTTP {response.status}. {detail}')
                    body = response.read(2_000_001)
                    if len(body) > 2_000_000:
                        raise ValueError('Resposta excede o limite de 2 MB.')
                    outcome['result'] = json.loads(body)
            except Exception as error:
                outcome['error'] = error
            finally:
                connection.close()
                finished.set()
        deadline = time.monotonic() + timeout
        if self.cancel is not None and self.cancel.is_set():
            raise RuntimeError('Operação cancelada; conteúdo anterior preservado.')
        threading.Thread(target=request, daemon=True).start()
        while not finished.wait(0.05):
            cancelled = self.cancel is not None and self.cancel.is_set()
            if cancelled or time.monotonic() >= deadline:
                aborted.set()
                wire = outcome.get('socket')
                if wire is not None:
                    try:
                        wire.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                connection.close()
                raise RuntimeError('Operação cancelada; conteúdo anterior preservado.' if cancelled else f'Ollama excedeu o limite total de {timeout} segundos. Tente um modelo menor ou um roteiro mais curto.')
        error = outcome.get('error')
        if error:
            if isinstance(error, RuntimeError):
                raise error
            raise RuntimeError('Não foi possível obter resposta válida do Ollama local. Inicie o Ollama, confira o endereço e tente novamente.') from error
        result = outcome.get('result')
        if not isinstance(result, dict) or result.get('error'):
            raise RuntimeError('Ollama retornou um erro ou resposta inválida; confira o modelo local.')
        return result

    def list_models(self):
        models = self._request('/api/tags', timeout=5).get('models')
        if not isinstance(models, list) or any(not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip() for item in models):
            raise ValueError('Lista de modelos inválida recebida do Ollama.')
        return sorted({item['name'] for item in models})

    def _generate(self, prompt, structured=False, schema=None, images=None, options=None):
        if not self.model:
            raise ValueError('Selecione um modelo Ollama instalado.')
        payload = {'model': self.model, 'prompt': prompt, 'stream': False}
        if images:
            # Ollama's local API receives base64 payloads. Files stay on the
            # user's machine; the endpoint constructor already rejects
            # non-loopback hosts.
            payload['images'] = [base64.b64encode(bytes(image)).decode('ascii') for image in images]
        if structured:
            payload['format'] = schema if schema is not None else 'json'
        if schema is not None:
            payload['options'] = {'temperature': 0, **(options or {})}
        result = self._request('/api/generate', payload)
        text = result.get('response')
        if result.get('done') is not True or result.get('done_reason') == 'length':
            raise ValueError('Ollama retornou uma geração incompleta. Reduza o roteiro ou use outro modelo.')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Ollama retornou conteúdo vazio.')
        return text.strip()

    def complete(self, prompt: str) -> str:
        return self._generate(prompt)

    def complete_json(self, prompt: str) -> dict:
        return self._complete_object(prompt)

    def complete_structured(self, prompt: str, schema: dict) -> dict:
        return self._complete_object(prompt, schema)

    def complete_image_structured(self, prompt: str, schema: dict, image: bytes) -> dict:
        """Ask a locally installed vision-capable Ollama model for JSON."""
        if not isinstance(image, (bytes, bytearray)) or not image:
            raise ValueError('Imagem inválida para análise visual local.')
        if len(image) > 12 * 1024 * 1024:
            raise ValueError('Imagem muito grande para análise visual local (limite de 12 MB).')
        try:
            # Small local vision models may need more than their conservative
            # default generation limit to close a schema-valid JSON response.
            # This raises only the output cap; it never changes temperature or
            # sends media outside the local Ollama endpoint.
            result = json.loads(self._generate(
                prompt, structured=True, schema=schema, images=[image], options={'num_predict': 256},
            ))
        except json.JSONDecodeError as exc:
            raise ValueError('O modelo visual não retornou JSON válido.') from exc
        if not isinstance(result, dict):
            raise ValueError('O modelo visual deve retornar um objeto JSON.')
        return result

    def _complete_object(self, prompt, schema=None):
        try:
            result = json.loads(self._generate(prompt, structured=True, schema=schema))
        except json.JSONDecodeError as exc:
            raise ValueError('O modelo não retornou JSON válido. O conteúdo anterior foi preservado.') from exc
        if not isinstance(result, dict):
            raise ValueError('O modelo deve retornar um objeto JSON.')
        return result
