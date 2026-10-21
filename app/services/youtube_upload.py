from pathlib import Path

class YouTubeUploader:
    """Integração opcional; só executa após credenciais OAuth fornecidas pelo operador."""
    def __init__(self, client_secret: Path, token_file: Path): self.client_secret,self.token_file=client_secret,token_file
    def upload(self, video: Path, title: str, description: str) -> str:
        if not self.client_secret.is_file(): raise FileNotFoundError('Arquivo OAuth client_secret.json não configurado.')
        if not video.is_file(): raise FileNotFoundError('Vídeo não encontrado.')
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc: raise RuntimeError('Instale google-api-python-client e google-auth-oauthlib para publicar.') from exc
        flow=InstalledAppFlow.from_client_secrets_file(str(self.client_secret),['https://www.googleapis.com/auth/youtube.upload'])
        credentials=flow.run_local_server(port=0); self.token_file.write_text(credentials.to_json(),encoding='utf8')
        youtube=build('youtube','v3',credentials=credentials)
        request=youtube.videos().insert(part='snippet,status',body={'snippet':{'title':title,'description':description},'status':{'privacyStatus':'private'}},media_body=MediaFileUpload(str(video)))
        return request.execute()['id']
