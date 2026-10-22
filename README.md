# Lore Oculta Studio

Aplicativo local para transformar um tema ou roteiro e imagens próprias em um YouTube Short vertical, com narração, legendas e MP4.

## Comece aqui

No PowerShell, dentro da pasta do projeto:

```powershell
uv sync
uv run python -m app.main
```

Se você usa o ambiente virtual já criado:

```powershell
.\.venv\Scripts\Activate.ps1
python -m app.main
```

## Criar um Short

1. Abra **Criar Short**.
2. Informe um tema, cole o roteiro ou os dois. Se o roteiro estiver vazio, o Ollama local pode criar um rascunho a partir do tema.
3. Clique em **Importar imagens** e escolha imagens suas ou licenciadas.
4. Clique em **Gerar Short**.

O aplicativo gera a narração, sincroniza as legendas e exporta o MP4 vertical. O vídeo fica na pasta do projeto, indicada na aba **Projetos**; normalmente é o arquivo `video_final.mp4`.

Para ajustar somente um item, use **Mídias**: importar ou trocar imagens, escolher capa, adicionar uma trilha e exportar novamente. As imagens são enquadradas em 1080×1920 sem cortar bordas; imagens horizontais ou quadradas recebem faixas neutras.

### Criar vários Shorts de um vídeo longo

Em **Criar Short > Ferramentas opcionais**, use **Criar Shorts de vídeo longo automaticamente**. Selecione um arquivo local e informe quantos Shorts deseja (o padrão é 5). O Studio copia a fonte para um projeto-pai, extrai o áudio, cria uma transcrição com timestamps reais por palavra, escolhe trechos falados e renderiza cada Short em seu próprio projeto, com legendas ASS sincronizadas e o áudio original.

Quando o Ollama local estiver configurado, o diretor editorial apenas classifica trechos que já foram delimitados pela fala real; ele não pode inventar falas ou alterar os limites. Sem Ollama, o Studio usa a seleção determinística registrada no manifesto `shorts-planejados.json`. Um erro em um corte não apaga os demais: cada projeto-filho conserva o próprio MP4, log e `technical_report.json`.

### Capas

Em **Mídias > Opções avançadas de edição**, **Gerar capa com IA** usa o Ollama local para propor uma imagem já importada, texto curto, preset e composição. A justificativa da escolha fica visível e os campos continuam editáveis: você pode trocar a imagem, o texto, a posição, o estilo, a intensidade e a moldura antes de gerar novamente. A capa é exportada em `capa.png` (1080×1920), sem alterar o MP4. Se o Ollama não estiver disponível, use **Escolher e gerar capa** para o fluxo totalmente manual/offline.

## Edição profissional e rastreabilidade

O preset editorial salvo (Neutral, Cinematic, Dynamic, Suspense ou Emotional) torna movimentos e cortes reproduzíveis. Imagens horizontais e quadradas preservam o primeiro plano integral sobre um fundo desfocado da própria mídia; não há crop automático do conteúdo enviado.

Cada exportação preserva o MP4 anterior até validar o novo arquivo. A pasta do projeto recebe `render_manifest.json` (timeline, hashes, fontes e licenças) e `technical_report.json` (codec, dimensões, FPS, duração e decodificação de frames). SRT continua disponível para acessibilidade e o MP4 exige ASS sincronizado por palavra, com destaque da palavra ativa.

## Trilha adaptativa

Na aba **Mídias**, use **Adicionar ou trocar trilha** depois de gerar a narração. A faixa é validada e copiada para dentro do projeto, portanto o Short continua reexportável mesmo se o arquivo original for movido. A mixagem é local: acompanha a duração da narração, aplica fades, normalização, limiter e abaixa automaticamente a música enquanto há voz. O controle de volume vai de 0 a 100% e inicia em 25%. Use **Ouvir prévia do áudio final** antes de exportar para conferir voz e trilha; o MP4 só é aceito quando possui stream AAC válido. Os cortes das imagens usam batidas quando elas forem detectáveis e usam distribuição uniforme segura quando não forem. Remover ou trocar a faixa deixa somente o vídeo pendente. Você é responsável por ter os direitos de uso da música escolhida.

Em **Opções avançadas de edição**, o botão **Selecionar catálogo de efeitos** aceita um JSON local e copia seus arquivos para a pasta do projeto. Nenhum som é baixado, criado ou aplicado sem uma licença declarada. Exemplo de catálogo:

```json
[
  {"intent": "impact", "title": "Impacto curto", "file": "impact.wav", "license": "CC0"},
  {"intent": "suspense", "title": "Tensão", "file": "suspense.wav", "license": "Licença própria"}
]
```

Os valores aceitos para `intent` são: `impact`, `suspense`, `revelation`, `transition`, `emotional` e `climax`. O arquivo precisa estar na mesma pasta do JSON ou em uma subpasta dela. O Studio só usa um efeito se a timeline tiver aquele evento editorial.

## O que você precisa

O fluxo básico exige:

- Imagens próprias ou com licença de uso.
- FFmpeg para exportar o MP4.
- Uma voz configurada: Kokoro é o padrão local; Piper continua disponível como alternativa offline.

São opcionais:

- **Ollama**: cria rascunhos, cenas e metadados localmente.
- **ComfyUI**: gera imagens localmente quando você não quiser importar as suas. Requer ComfyUI em `http://127.0.0.1:8188` e um checkpoint compatível.

Abra **Configurações** apenas quando precisar configurar essas integrações, trocar a voz ou personalizar caminhos. O aplicativo informa ali quando algum componente opcional não está disponível.

## Narração

O projeto salva o motor e a voz usados em cada Short. Você pode testar uma voz antes de gerar e regenerar somente a narração. Ao mudar a voz, as legendas e a duração das cenas são atualizadas com o novo áudio.

O roteiro visível não é alterado para melhorar pronúncia. Ajustes fonéticos, velocidade e pausa entre frases afetam apenas o áudio.

## Dados e privacidade

Projetos, configurações, banco e logs ficam em:

```text
%LOCALAPPDATA%\Gabriel Ramalhos\Lore Oculta Studio
```

Uma atualização não sobrescreve esses dados. Integrações como Ollama, ComfyUI e Piper são locais. Use conteúdo, imagens, vozes e músicas para os quais você tenha autorização.

## Desenvolvimento

```powershell
uv sync --extra test
uv run pytest -q
```

O empacotamento comercial para Windows, avisos de terceiros, privacidade e licença estão em [packaging/DISTRIBUTION.md](packaging/DISTRIBUTION.md). O instalador não inclui projetos do usuário, Ollama, ComfyUI, Piper ou modelos não aprovados.

No aplicativo, o menu **Ajuda** mostra versão, documentos, caminho dos dados locais e permite exportar um diagnóstico sanitizado. Para orientações comerciais provisórias e pendências externas, veja [a política](packaging/COMMERCIAL_POLICY.md) e o [checklist](packaging/EXTERNAL_RELEASE_CHECKLIST.md); ambos exigem revisão jurídica antes de distribuição.

## Plano editorial local

### Preset Lore Oculta — Retenção Máxima

Use este preset com **6 a 12 imagens** na sequência. Todas as imagens importadas nessa faixa são preservadas e distribuídas pela duração real da narração; o Studio não descarta mídias para forçar seis cenas. O preset aplica o plano local de retenção, prioriza movimentos e transições moderados, usa o perfil de trilha **Cinematográfica** e cria eventos editoriais de impacto, tensão e clímax. Eventos só entram no áudio se você cadastrar arquivos locais licenciados para eles; sem um ativo declarado, a exportação registra a omissão e não utiliza uma substituição automática.

As legendas continuam em SRT para acessibilidade. A exportação usa ASS com palavras brancas, palavra corrente em amarelo, contorno/sombra e sem faixa preta. O arquivo `legendas.words.json` registra os tempos reais do ASR local e a confiança quando o backend a disponibiliza. Se o ASR não produzir tempos reais de palavra, a geração para e deixa claro que a sincronização falhou: o Studio não distribui palavras proporcionalmente nem exporta legenda aproximada por frase. Use **Ajustar sincronização por palavra** para correções manuais de um alinhamento válido; isso invalida apenas o vídeo.

### Presets e gancho

Além dos estilos existentes, **História sombria**, **Top 5** e **Teoria e mistério** definem movimentos, transições, perfil de mixagem e intenções de efeitos reproduzíveis. Eles preservam o roteiro e acompanham a duração real da narração.

Em **Criar Short**, **Analisar gancho com IA local** usa o Ollama apenas para sugerir até três aberturas alternativas baseadas no texto: conflito, consequência e curiosidade. Ele não altera o roteiro, não prevê retenção e rejeita respostas que introduzam entidades ausentes no texto.

No fluxo de **Gerar Short**, com Ollama local disponivel, o plano e criado automaticamente depois da narracao: tempos, movimentos e transicoes usam o roteiro, a duracao real do audio e a ordem das imagens. Se o Ollama estiver indisponivel ou responder de forma invalida, a exportacao continua com o preset seguro. Uma timeline manual nunca e substituida; para refazer o plano deliberadamente, use a secao avancada de timeline em **Midias**.

Opcionalmente, em **Configurações**, ative **análise visual local** e informe o nome de um modelo visual já instalado no Ollama. O Studio envia cada imagem apenas ao servidor Ollama em `localhost`, cria descrições curtas de elementos visíveis e as entrega ao diretor editorial. Quando a análise for concluída, o diretor pode organizar cada imagem importada uma única vez na ordem que melhor ilustra a narração; essa decisão fica registrada na timeline. O roteiro nunca é alterado. Sem análise visual disponível, a ordem de importação é preservada e o plano continua usando roteiro e duração real da narração.

Em **Mídias**, importe todas as imagens de uma vez. Depois da narração, use **Analisar imagens e montar cenas com IA** para criar o plano de tempos, movimentos, transições e notas. Com a análise visual local configurada, o diretor também relaciona as imagens aos trechos mais adequados; sem ela, preserva a ordem importada. A seção **Ajustar cenas manualmente** fica recolhida e serve apenas para uma correção pontual. Falha ou resposta inválida preserva o plano anterior, e a exportação continua com o preset determinístico.
