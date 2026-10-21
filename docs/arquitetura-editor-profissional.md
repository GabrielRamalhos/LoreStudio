# Editor profissional para Shorts

A timeline `timeline/v1` persistida em `Project.settings` é o plano de render. Ela usa somente caminhos relativos ao projeto, define FPS, clipes, movimento, transição, legendas e eventos de áudio. Projetos antigos recebem uma timeline equivalente no primeiro render, sem migração destrutiva.

Cada clipe pode registrar `start`/`end` em segundos, `narration_text` e `editing_direction`. Quando os intervalos estão presentes, o compilador usa suas durações em vez de redistribuir as imagens. Texto e direção pertencem somente ao plano editorial e nunca substituem o roteiro persistido; projetos sem intervalos continuam com a divisão automática compatível.

O FFmpeg compõe cada imagem em 1080x1920: o primeiro plano é mantido inteiro sobre um fundo desfocado da mesma mídia. A publicação é transacional: a saída é renderizada em diretório temporário, validada como H.264/AAC/yuv420p e somente então substitui `video_final.mp4`. Falhas preservam o último MP4 válido.

`legendas.srt` é sempre o artefato acessível. `legendas.ass` é obrigatório para exportação e é emitido somente quando o ASR fornece tempos de palavra coerentes; não há inferência de tempos nem fallback visual por frase. `render_manifest.json` registra hashes de dependências, fontes/licenças declaradas e o relatório técnico para reprodução local.

As mudanças invalidam somente o derivado afetado: roteiro/voz invalidam voz, legendas e vídeo; legendas invalidam legendas e vídeo; imagens, timeline, preset, trilha e volume invalidam apenas vídeo. Eventos sem ativo licenciado são omitidos e registrados, sem substituir mídia automaticamente.

## Diretor editorial local

### Retenção Máxima

`retencao_maxima` é um contrato de preset local: exige seis fontes, 30 FPS e usa a duração efetiva do WAV. A timeline automática serializa intenções de áudio (`impact`, `suspense`, `climax`) com tempos reais. Um catálogo local de efeitos licenciados pode fornecer ativos para os intents `impact`, `suspense`, `revelation`, `transition`, `emotional` e `climax`; os arquivos são validados e copiados para `efeitos/` dentro do projeto antes de serem vinculados. Um evento só é renderizado quando tem ativo e licença declarada; a ausência de SFX não altera a narração nem introduz mídia de terceiros.

O relatório técnico registra a modalidade da legenda (`word`), perfil de mixagem, presença de trilha, parâmetros de ducking/limiter e eventos omitidos. Esses dados são evidência de configuração; não representam uma promessa subjetiva de volume ou uma medição de loudness não executada.

No fluxo normal de **Gerar Short**, o diretor e chamado somente depois que o WAV real existe e antes da renderizacao. Ele recebe apenas roteiro, caminhos relativos ordenados das imagens, duracao e preset; nao analisa nem envia as imagens. O resultado so e persistido se a entrada nao mudou e toda a timeline passar pela validacao. Indisponibilidade do Ollama ou resposta invalida registra uma nota e mantem a exportacao deterministica; cancelar interrompe o trabalho antes do video.

O diretor opcional usa exclusivamente o Ollama local e recebe roteiro, duração real da narração e os caminhos relativos das imagens importadas. Sem modelo visual, é planejamento por nomes de arquivo e a ordem importada é imutável. Com a análise visual local habilitada, o modelo descreve elementos visíveis de cada arquivo e o diretor pode atribuir as imagens aos trechos mais adequados, sempre usando cada fonte exatamente uma vez e registrando `visual_ordering` na proveniência. A resposta estruturada torna-se `timeline/v1` somente após validar fontes permitidas, um clipe por imagem, início zero, intervalos contínuos e fim igual ao WAV.

A timeline salva versão do diretor, modelo local, hash estável de entrada e hash do plano. Falha, cancelamento ou JSON inválido mantêm a timeline anterior; o preset determinístico continua disponível. O usuário pode revisar a tabela; uma nova narração conserva a intenção editorial pela reescala proporcional existente.
