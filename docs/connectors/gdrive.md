# Google Drive → pasta de entrada do filing

`jurisync.connectors.gdrive` baixa uma pasta do Google Drive (e, se preciso, arquivos soltos de outras pastas) direto
para o disco, no formato do contrato de entrada do
filing (`docs/connectors/pje-trf5-filing.md`, seção do contrato), para depois rodar `pje_filing_prepare`. É uma peça
separada do filing, como o exportador do ADVBOX: só depende do contrato; o filing não conhece o Drive.

Por que não o conector do Drive do Claude: ele devolve os arquivos dentro da conversa (em base64) e só funciona para o
filing numa sessão com terminal que os decodifique. No Claude Chat, sem terminal, o caminho é este exportador, que fala
direto com a API do Drive.

## Como funciona

- **Acesso:** uma **conta de serviço** do Google, com o escopo **`drive.readonly`** (só leitura; nunca escreve no
  Drive). Ela enxerga apenas as pastas compartilhadas com o e-mail dela, inclusive em Drives compartilhados.
- **Chave:** o arquivo JSON da conta de serviço, no caminho da variável **`GOOGLE_SERVICE_ACCOUNT_FILE`**; sem ela,
  **`~/.config/jurisync/google-service-account.json`**. A chave é recusada se outras pessoas puderem lê-la (precisa
  de `chmod 600`).
- **Dependências:** nenhuma nova. O token é obtido pelo fluxo JWT da conta de serviço, assinado com `cryptography`
  (que o jurisync já usa), e as chamadas HTTP usam o `urllib` da biblioteca padrão. As bibliotecas do Google
  (`google-auth`, `google-api-python-client`) trariam várias dependências para três chamadas simples.
- **Exportação:** lista a pasta (todas as páginas), baixa cada arquivo para uma pasta local **nova** (pasta 0700,
  arquivos 0600), exporta Google Docs, Planilhas, Apresentações e Desenhos para PDF, confere o `md5Checksum` do Drive
  contra o que foi baixado e grava o `sources.json` com `origin: "drive"`, id do arquivo, caminho de pastas, nome
  original, data de modificação, link, `verifiedBy: "jurisync.gdrive"` e o `sha256` do arquivo gravado. O filing
  registra esses arquivos como origem **exported** e reconfere o sha256 ao copiar para a `inbox/`.
- **Limites e recusas:** arquivo acima de `maxFileMb` (padrão 50 MB) falha a exportação, antes ou durante o download;
  subpastas só entram com `includeSubfolders`; `exclude` aceita padrões de nome ou caminho (`*.docx`, `Rascunhos/*`);
  formulários e outros tipos do Google que não exportam para PDF ficam de fora e aparecem em `skipped`. A pasta de
  destino nunca fica dentro de pasta sincronizada (Google Drive para desktop, Dropbox, OneDrive, iCloud) e, se a
  exportação falhar no meio, nada fica para trás.
- **Destino:** `<JURISYNC_FILING_DIR>/inputs/drive-<id>-<data>` (padrão `~/.local/share/jurisync/filing/inputs`).
  A pasta sai junto com o run em `pje_filing_cleanup`.

## Configuração (uma vez)

1. No [Google Cloud Console](https://console.cloud.google.com/), crie (ou escolha) um projeto.
2. Em **APIs e serviços → Biblioteca**, ative a **Google Drive API**.
3. Em **IAM e administrador → Contas de serviço**, crie uma conta de serviço (ex.: `jurisync-drive`). Não precisa
   dar papéis no projeto.
4. Na conta criada, **Chaves → Adicionar chave → Criar nova chave → JSON**. Salve o arquivo e proteja-o:

   ```bash
   mkdir -p ~/.config/jurisync && mv ~/Downloads/<arquivo>.json ~/.config/jurisync/google-service-account.json
   ```

   ```bash
   chmod 600 ~/.config/jurisync/google-service-account.json
   ```

   (Para outro caminho, defina `GOOGLE_SERVICE_ACCOUNT_FILE` no `.env` do jurisync.)
5. No Google Drive, compartilhe a pasta dos clientes (ou a pasta de cada cliente) com o **e-mail da conta de serviço**
   (`...@<projeto>.iam.gserviceaccount.com`) como **Leitor**. Em Drive compartilhado, adicione-a como membro com acesso
   de Leitor.
6. Teste a chave e o acesso:

   ```bash
   .venv/bin/python -m jurisync.connectors.gdrive check --folder "https://drive.google.com/drive/folders/<id>"
   ```

   ou, no Claude, a ferramenta **`drive_check_access`** (com ou sem `folder`). Ela devolve o e-mail da conta, que é o
   endereço com que as pastas devem ser compartilhadas.

## Habilitar no Claude

É um servidor MCP próprio, **`jurisync-gdrive`** (instalado junto com os outros comandos do jurisync), também
disponível em `jurisync-http gdrive`. No Claude Code deste repositório ele já está no `.mcp.json` como `gdrive`.

No **Claude Desktop (Chat e Code)**, acrescente dentro de `"mcpServers"` do
`~/Library/Application Support/Claude/claude_desktop_config.json`, ao lado de `"pje"`:

```json
"gdrive": {
  "command": "/Users/jeanferreira/.local/bin/jurisync-gdrive",
  "args": [],
  "env": {}
}
```

Não precisa de `env`: a chave é lida de `~/.config/jurisync/google-service-account.json`. Só para guardá-la em outro
lugar acrescente `"env": {"GOOGLE_SERVICE_ACCOUNT_FILE": "/caminho/da/chave.json"}`. Depois, reinicie o Claude.

Fora do repositório, no Claude Code:

```bash
claude mcp add gdrive -- jurisync-gdrive
```

## Uso: Drive → filing (Claude Chat, sem terminal)

1. `drive_export_filing_folder` com `folder` (link da pasta do cliente, ex.: a pasta dele em "Documentos Clientes
   BPC") e, em `files`, links de arquivos que estão em outra pasta (ex.: a petição em "1. Iniciais - BPC
   (Controladoria)"). `includeSubfolders` se os documentos estiverem em subpastas; `exclude` para deixar algo de fora
   (ex.: `["*protocolo*"]`). Ela devolve `sourceFolder`, `inputFolder` e o que ficou de fora (`skipped`).
2. `pje_filing_prepare` com `profile`, um `runId` novo, os `facts` e esse `sourceFolder` (ou `inputFolder`) sem mudar
   nada. A revisão mostra a origem de cada documento (Drive, com link e pasta).
3. `pje_filing_review` → `pje_filing_build` → resumo ao usuário → `pje_filing_approve` só com o "sim" dele →
   `pje_draft_create` → `pje_draft_status` (no fim, `checkPje: true`).
4. Depois do protocolo (pelo usuário), `pje_filing_cleanup` com o `runId` apaga o run e a pasta exportada.

Se a conta não enxergar a pasta ou o arquivo, o erro diz o e-mail com que compartilhar (Leitor); sem chave, diz onde
salvá-la.

Pelo terminal:

```bash
.venv/bin/python -m jurisync.connectors.gdrive export --folder "<id ou URL>" --file "<URL da petição>" --subfolders
```

## Erros comuns

| Mensagem | O que fazer |
| --- | --- |
| `No Google service-account key at ...` | Salvar a chave no caminho padrão ou definir `GOOGLE_SERVICE_ACCOUNT_FILE`. |
| `... can be read by others ...; run: chmod 600 ...` | Rodar o `chmod 600` indicado. |
| `Google refused the service-account key (HTTP 400/401)` | Chave revogada ou de outro projeto; gerar outra. |
| `... is not shared with the service account <e-mail>. Share it ... as Viewer (Leitor)` | Compartilhar a pasta com esse e-mail como Leitor. |
| `The Google Drive API is not enabled ...` | Ativar a Google Drive API no projeto da conta de serviço. |
| `... more than the N MB limit` | Aumentar `maxFileMb` (ou `--max-mb`) se o arquivo for mesmo necessário, ou excluí-lo. |
| `md5 mismatch` | O download chegou corrompido; repetir. |
