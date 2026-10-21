"""Cliente HTTP exclusivo para uma instalação local do ComfyUI."""
import http.client
import ipaddress
import json
import time
import urllib.parse

class ComfyUIProvider:
    def __init__(self, url, workflow, checkpoint=''):
        part=urllib.parse.urlsplit(url.strip())
        try:
            local = part.hostname == 'localhost' or ipaddress.ip_address(part.hostname or '').is_loopback
        except ValueError:
            local = False
        if part.scheme != 'http' or not local or part.username or part.password or part.query or part.fragment: raise ValueError('Configure uma URL HTTP local do ComfyUI.')
        if not workflow: raise ValueError('Configure o workflow do ComfyUI antes de gerar imagens.')
        self.url=urllib.parse.urlunsplit((part.scheme, part.netloc, part.path.rstrip('/'), '', '')); self.workflow=workflow; self.checkpoint=checkpoint
    def _request(self, method, path, payload=None):
        part=urllib.parse.urlsplit(self.url); conn=http.client.HTTPConnection(part.hostname,part.port or 80,timeout=20)
        try:
            data=json.dumps(payload).encode() if payload is not None else None; conn.request(method,path,data,{'Content-Type':'application/json'} if data else {})
            result=conn.getresponse(); body=result.read()
            if result.status != 200: raise RuntimeError(f'ComfyUI retornou HTTP {result.status}. Verifique servidor e workflow.')
            return body
        except OSError as error:
            raise RuntimeError('Não foi possível conectar ao ComfyUI local. Inicie o servidor em http://127.0.0.1:8188 e tente novamente.') from error
        finally: conn.close()
    def _cancel(self, prompt_id):
        try:
            self._request('POST', '/queue', {'delete': [prompt_id]})
        except Exception:
            pass

    def generate(self,prompt,cancel=None):
        workflow=json.loads(self.workflow) if isinstance(self.workflow,str) else self.workflow
        if not isinstance(workflow,dict): raise ValueError('Workflow do ComfyUI inválido.')
        for node in workflow.values():
            inputs=node.get('inputs',{}) if isinstance(node,dict) else {}
            for key in ('text','prompt'):
                if key in inputs: inputs[key]=prompt
            if self.checkpoint and 'ckpt_name' in inputs: inputs['ckpt_name']=self.checkpoint
        ident=json.loads(self._request('POST','/prompt',{'prompt':workflow})).get('prompt_id')
        if not ident: raise RuntimeError('ComfyUI não retornou identificador da tarefa.')
        while True:
            if cancel is not None and cancel.is_set():
                self._cancel(ident)
                raise RuntimeError('Operação cancelada pelo usuário.')
            record=json.loads(self._request('GET','/history/'+urllib.parse.quote(str(ident)))).get(ident,{})
            for output in record.get('outputs',{}).values():
                images=output.get('images',[]) if isinstance(output,dict) else []
                if images:
                    image=images[0]; query=urllib.parse.urlencode({'filename':image['filename'],'subfolder':image.get('subfolder',''),'type':image.get('type','output')})
                    return self._request('GET','/view?'+query)
            if record.get('status',{}).get('status_str')=='error': raise RuntimeError('ComfyUI falhou ao renderizar a cena.')
            time.sleep(.15)
