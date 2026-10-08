# Instalação do MCP INSS GERID/PAT

O conector `inss-gerid` consulta notificações e tarefas do PAT/INSS usando o
certificado A3 da própria máquina. Ele precisa rodar localmente: o token A3, o
middleware e a janela nativa do PIN ficam no computador do usuário (Docker não
é recomendado, pois não recebe o token USB nem a janela do PIN).

Validado no macOS. Windows e Linux dependem do suporte do fabricante do A3.

## Requisitos

- Google Chrome instalado;
- certificado A3 válido, conectado, com o middleware oficial do fabricante;
- PIN do certificado conhecido pelo titular;
- QR do autenticador GERID (ou o segredo TOTP), guardado em local privado;
- Python 3.12+ e [uv](https://docs.astral.sh/uv/);
- Claude Code ou Claude Desktop.

O MCP não digita o PIN: ele aparece numa janela nativa do sistema/middleware.

## 1. Instalar

Na pasta do projeto:

```bash
uv sync
```

```bash
cp .env.example .env
```

O conector usa o Chrome instalado; não é preciso baixar navegadores do Playwright.

## 2. Configurar `.env`

O QR exportado contém um segredo reutilizável: quem tiver o arquivo gera
códigos TOTP. Não o envie por e-mail nem o coloque no Git ou em pasta
compartilhada.

```dotenv
INSS_GERID_QR_IMAGE="/Users/SEU_USUARIO/Library/Application Support/Jurisync/QR_gerid.png"
INSS_GERID_TOTP_ACCOUNT=1
INSS_BROWSER_PROFILE="/Users/SEU_USUARIO/Library/Application Support/Jurisync/ChromeProfile"
```

- Use um perfil do Chrome **dedicado e persistente**, nunca o perfil pessoal
  (o Chrome 136+ bloqueia depuração remota no diretório padrão).
- `INSS_GERID_TOTP_ACCOUNT` escolhe a conta do QR, começando em 1.
- Alternativa ao QR: `INSS_GERID_TOTP_SECRET` com o segredo Base32 ou uma URI
  `otpauth://` (mesmo cuidado de segurança).
- Opcionais: `INSS_CHROME_PATH`, `INSS_CDP_PORT` (padrão 9338),
  `INSS_GERID_TIMEOUT_SECONDS` (padrão 180).

Restrinja as permissões:

```bash
chmod 600 .env "/Users/SEU_USUARIO/Library/Application Support/Jurisync/QR_gerid.png"
```

## 3. Primeiro login

```bash
.venv/bin/python cli/inss_probe.py login --keep-open --timeout 180
```

Fluxo esperado: o Chrome dedicado abre, escolhe "Certificado digital", o
sistema pede o PIN, o conector envia o TOTP, seleciona abrangência e papel,
confirma o aviso A3 e o terminal mostra `"authenticated": true`. Pressione
Enter para fechar.

No macOS o conector configura a política `AutoSelectCertificateForUrls` do
Chrome para `geridinss.dataprev.gov.br:8443`, evitando a escolha manual quando
há um único certificado elegível. O PIN continua sendo pedido.

## 4. Validar a sessão reutilizável

```bash
.venv/bin/python cli/inss_probe.py login --headless
```

```bash
.venv/bin/python cli/inss_probe.py notifications --headless
```

Com a credencial válida, o Chrome roda invisível. Expirada, ele fecha e reabre
visível para um novo login A3/PIN (o GERID não tem refresh token).
`--show-secrets` imprime credenciais e payloads: use só em diagnóstico controlado.

## 5. Conectar ao Claude

**Claude Code:** o `.mcp.json` do projeto já declara `inss-gerid`. Reinicie o
Claude Code na pasta do projeto.

**Claude Desktop:** em Configurações → Desenvolvedor → Editar configuração,
mescle (sem apagar o que já existe) usando caminhos absolutos:

```json
{
  "mcpServers": {
    "inss-gerid": {
      "command": "/CAMINHO/DO/PROJETO/.venv/bin/python",
      "args": ["-m", "jurisync.connectors.inss.server"],
      "env": { "JURISYNC_ENV_FILE": "/CAMINHO/DO/PROJETO/.env" }
    }
  }
}
```

Encerre o Claude Desktop com `Cmd+Q` e abra de novo.

Ferramentas: `inss_gerid_ensure_session`, `inss_list_notifications`,
`inss_search_tasks`, `inss_get_task`, `inss_get_attachment`, `inss_get_process`,
`inss_read_document`.

`inss_get_attachment` e `inss_get_process` baixam o arquivo para
`downloaded_files/inss/` e devolvem o texto do PDF já extraído (até `maxChars`,
padrão 40 000 caracteres). Se o documento for maior, a resposta traz
`nextOffset`: chame `inss_read_document` com o nome do arquivo salvo e esse
`offset` para ler o resto, sem novo login. O texto é extraído localmente com o
pdf-inspector; páginas digitalizadas passam por OCR local (desligue com
`INSS_PDF_OCR=false`). Nenhum arquivo é enviado a serviço externo: páginas que a
biblioteca marca para OCR hospedado são apenas reportadas. Variáveis opcionais:
`INSS_MAX_PDF_TEXT_CHARS` (limite total extraído, padrão 500 000) e
`INSS_DOWNLOAD_DIR` (pasta dos downloads).

## Solução de problemas

| Sintoma | O que verificar |
|---|---|
| `Chrome exited before its CDP endpoint became available` | Perfil dedicado já aberto, porta ocupada (`lsof -nP -iTCP:9338 -sTCP:LISTEN`; troque `INSS_CDP_PORT`), caminho do Chrome errado, perfil sem permissão de escrita |
| Certificado não aparece | Token conectado, middleware reconhece o certificado, validade e cadeia; reinicie Chrome/Claude após instalar o middleware; teste o login GERID manualmente; veja `chrome://policy` |
| Sempre volta para o login | Todas as execuções devem usar o mesmo `INSS_BROWSER_PROFILE`; a sessão é renovada quando faltam menos de 30 s para expirar |
| QR não reconhecido | Imagem nítida e completa; `INSS_GERID_TOTP_ACCOUNT` na posição certa |
| Sem opções de papel/abrangência | Confirme com o administrador GERID que o usuário tem papel válido no PAT |
| PDF sem texto (`pagesNeedingOcr`) | PDF digitalizado; o arquivo fica salvo, mas OCR não está configurado |
| MCP não aparece | JSON válido, caminhos absolutos, `uv sync` sem erro, reinício completo; veja os logs do Claude |

## Segurança

- QR, `.env`, perfil do Chrome e PDFs fora do Git, com acesso só do usuário local;
- nunca registre PIN, segredo TOTP, `ifs_auth`, cookies ou tokens;
- um perfil por usuário; nunca o perfil pessoal;
- retire o A3 quando a máquina ficar desacompanhada;
- em caso de suspeita de vazamento, revogue credenciais e troque o QR.
