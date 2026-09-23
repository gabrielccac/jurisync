# Guia de instalação do MCP INSS GERID/PAT

Este guia instala o conector local `inss-gerid` no macOS para que o Claude
Desktop consulte tarefas e notificações do PAT/INSS usando o certificado A3
conectado à própria máquina.

> Estado atual: o fluxo foi validado no macOS. Windows tem suporte básico no
> código, mas exige o middleware específico do certificado e ainda deve ser
> homologado por fornecedor. Linux depende do suporte do fabricante ao A3.

## Por que a instalação é local

O certificado A3, o middleware, o PIN e a janela nativa de autenticação ficam
na máquina do usuário. Por isso, este MCP precisa executar localmente.

Docker não é recomendado para este fluxo. No macOS, o container roda dentro de
uma VM e não recebe de forma transparente o token USB, o Keychain, o middleware
nem a janela nativa do PIN. Seria necessário configurar passthrough específico
para cada fabricante, sem eliminar a interação humana.

A evolução recomendada é empacotar o projeto como uma extensão `.mcpb`. O
Claude Desktop já oferece runtime Node.js para extensões, permitindo uma
instalação mais próxima de “um clique”. Até esse pacote existir, use os passos
manuais abaixo.

## Requisitos

- macOS com usuário autorizado a instalar aplicativos e middleware;
- Claude Desktop atualizado;
- Google Chrome instalado em `/Applications/Google Chrome.app`;
- Node.js 22 ou superior e npm;
- Python 3 e `venv`;
- certificado digital A3 válido;
- token/cartão A3 conectado fisicamente;
- middleware/driver oficial do fabricante instalado;
- PIN do certificado conhecido pelo usuário;
- imagem do QR do autenticador GERID, ou um segredo TOTP fornecido de forma
  segura;
- acesso à internet para os domínios do GERID e PAT/INSS.

Não é necessário instalar o Claude Code CLI. O erro `command not found: claude`
não impede o uso deste MCP no Claude Desktop.

## 1. Preparar o certificado A3

1. Identifique o fabricante do token ou cartão.
2. Baixe e instale o middleware oficial compatível com a versão do macOS.
3. Reinicie o macOS se o instalador solicitar.
4. Conecte o dispositivo A3 diretamente à máquina.
5. Abra o gerenciador do middleware e confirme que ele reconhece o dispositivo
   e mostra um certificado válido.
6. Confirme que o usuário conhece o PIN. Não coloque o PIN em `.env`, JSON,
   script ou gerenciador de senhas acessível ao MCP.

O MCP pode automatizar a seleção do certificado elegível no Chrome, mas não
digita o PIN. O PIN aparece em uma janela nativa controlada pelo macOS ou pelo
middleware. A eventual retenção temporária do PIN também é controlada pelo
middleware, não pelo MCP.

No primeiro uso ou depois que a sessão GERID expirar, o Chrome será aberto de
forma visível e o usuário deverá informar o PIN. Enquanto a credencial GERID
continuar válida, as consultas seguintes usam o perfil persistente em modo
headless.

## 2. Instalar Node.js, npm e Python

Instale uma versão LTS atual do Node.js pelo instalador oficial:

- <https://nodejs.org/en/download>

O npm é instalado junto com o Node.js. Depois, abra o Terminal e valide:

```bash
node --version
npm --version
python3 --version
which node
```

`node --version` precisa mostrar `v22` ou superior. Guarde o resultado de
`which node`; o Claude Desktop precisará do caminho absoluto porque aplicativos
abertos pela interface gráfica nem sempre carregam o PATH do `nvm` ou do shell.

Se `python3` não existir, instale-o por <https://www.python.org/downloads/macos/>
ou pelo gerenciador de pacotes usado pela organização.

## 3. Colocar os arquivos do MCP

Descompacte `inss-gerid-mcp.zip` dentro da pasta do projeto. Ao final, estes
arquivos devem existir:

```text
integrations/inss/client.mjs
scripts/inss_mcp_server.mjs
scripts/inss_gerid_playwright.mjs
scripts/pje_2fa_from_qr.py
package.json
package-lock.json
requirements-inss.txt
inss.env.example
```

No Finder, abra a pasta, clique com o botão direito em uma área vazia e escolha
**Novo Terminal na Pasta**. Se a opção não aparecer, abra o Terminal e use:

```bash
cd "/caminho/absoluto/para/Jurisync-INSS-Test"
```

Todos os comandos seguintes devem ser executados nessa pasta.

## 4. Instalar as dependências

```bash
npm install
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-inss.txt
```

O projeto usa o Chrome já instalado na máquina. Não é necessário executar
`npx playwright install`.

Valide a instalação:

```bash
npm test
test -x .venv/bin/python && echo "Python virtual pronto"
```

## 5. Proteger e configurar o QR GERID

O QR exportado pelo autenticador contém um segredo reutilizável. Quem tiver
esse arquivo consegue gerar códigos TOTP. Não envie o QR por e-mail, não o
adicione ao Git e não o deixe em pasta compartilhada.

Coloque a imagem em um local privado. Por exemplo:

```text
/Users/SEU_USUARIO/Library/Application Support/Jurisync/QR_gerid.png
```

Se o QR tiver várias contas, `INSS_GERID_TOTP_ACCOUNT` seleciona a conta pela
posição, começando em `1`.

Crie o `.env` a partir do exemplo:

```bash
test -f .env || cp inss.env.example .env
```

Edite `.env`:

```dotenv
INSS_GERID_QR_IMAGE="/Users/SEU_USUARIO/Library/Application Support/Jurisync/QR_gerid.png"
INSS_GERID_TOTP_ACCOUNT=1
INSS_GERID_TIMEOUT_SECONDS=180
INSS_BROWSER_PROFILE="/Users/SEU_USUARIO/Library/Application Support/Jurisync/ChromeProfile"
```

Crie a pasta do perfil e restrinja os arquivos sensíveis:

```bash
mkdir -p "/Users/SEU_USUARIO/Library/Application Support/Jurisync/ChromeProfile"
chmod 600 .env "/Users/SEU_USUARIO/Library/Application Support/Jurisync/QR_gerid.png"
```

Use sempre um perfil dedicado e persistente. Não aponte
`INSS_BROWSER_PROFILE` para o perfil normal do Chrome: além de misturar cookies
e contas pessoais, Chrome 136 ou superior bloqueia depuração remota no diretório
de dados padrão.

Variáveis opcionais:

```dotenv
# Se o Chrome estiver em outro local:
# INSS_CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Se a porta 9338 estiver ocupada:
# INSS_CDP_PORT=9340

# Limites de download e texto extraído:
# INSS_MAX_DOWNLOAD_BYTES=52428800
# INSS_MAX_PDF_TEXT_CHARS=500000

# Use apenas se o perfil dedicado tiver um subperfil específico:
# INSS_BROWSER_PROFILE_DIRECTORY=Default
```

Como alternativa ao QR, `INSS_GERID_TOTP_SECRET` pode receber o segredo Base32
ou uma URI `otpauth://`. Essa opção exige o mesmo cuidado de segurança do QR.

## 6. Fazer o primeiro login

Feche apenas janelas antigas abertas pelo perfil de automação. O Chrome pessoal
pode continuar aberto porque o MCP usa outro diretório de dados.

Execute:

```bash
npm run inss:gerid-test -- --qr-image="/caminho/para/QR_gerid.png" --account=1 --keep-open --timeout=180
```

Fluxo esperado:

1. o Chrome dedicado abre;
2. o MCP escolhe “Certificado digital”;
3. o macOS ou middleware pode pedir a escolha do certificado;
4. o usuário informa o PIN na janela nativa;
5. o MCP gera e envia o TOTP;
6. o MCP seleciona abrangência e papel no PAT;
7. o MCP confirma o aviso A3;
8. o Terminal mostra `"authenticated": true`;
9. pressione Enter no Terminal para fechar o Chrome.

O script tenta configurar no macOS a política do Chrome
`AutoSelectCertificateForUrls` para `geridinss.dataprev.gov.br:8443`. Essa
política pode evitar a escolha manual quando há um único certificado elegível,
mas não elimina o PIN. Para inspecioná-la:

```bash
defaults read com.google.Chrome AutoSelectCertificateForUrls
```

Se houver mais de um certificado compatível, confirme cuidadosamente qual foi
selecionado.

## 7. Validar sessão reutilizável e API

Com a sessão válida, teste o modo headless:

```bash
npm run inss:gerid-test -- --headless --timeout=180
```

Teste a consulta de notificações:

```bash
npm run inss:notificacoes-test -- --direct --headless --timeout=180
```

Se a credencial estiver ausente ou expirada, o comportamento correto é fechar
o Chrome headless e reabrir uma janela visível para novo login A3/PIN. O MCP não
possui refresh token GERID; a reautenticação A3 é a renovação da sessão.

Evite `--show-secrets`: essa opção imprime credenciais e payloads sensíveis e
existe somente para diagnóstico controlado.

## 8. Configurar no Claude Desktop

Enquanto o projeto não for distribuído como `.mcpb`, use o servidor MCP local
via JSON. No Claude Desktop, abra as configurações de desenvolvedor e escolha a
opção para editar a configuração. No macOS, o arquivo normalmente fica em:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

Não substitua preferências já existentes. Adicione ou mescle somente a chave
`mcpServers`. Use caminhos absolutos e substitua os exemplos abaixo:

```json
{
  "mcpServers": {
    "inss-gerid": {
      "command": "/CAMINHO/RETORNADO/POR/which/node",
      "args": [
        "--env-file=/Users/SEU_USUARIO/Downloads/Jurisync-INSS-Test/.env",
        "/Users/SEU_USUARIO/Downloads/Jurisync-INSS-Test/scripts/inss_mcp_server.mjs"
      ],
      "env": {
        "CLAUDE_PROJECT_DIR": "/Users/SEU_USUARIO/Downloads/Jurisync-INSS-Test"
      }
    }
  }
}
```

Observações:

- o JSON não aceita comentários nem vírgula depois do último item;
- `command` deve ser o caminho absoluto retornado por `which node`;
- `.env`, `scripts/inss_mcp_server.mjs` e `CLAUDE_PROJECT_DIR` precisam apontar
  para a mesma instalação;
- se o arquivo já tiver `preferences` ou outros servidores, preserve-os;
- servidores locais configurados por JSON são específicos do Claude Desktop,
  não do site `claude.ai`.

Salve o arquivo, encerre completamente o Claude Desktop com `Cmd+Q` e abra-o
novamente. Fechar apenas a janela não reinicia o processo MCP.

No Claude Desktop, abra `+` > **Conectores**, ou as configurações de
desenvolvedor, e confirme que `inss-gerid` está conectado.

## 9. Testar as ferramentas no Claude

Peça primeiro:

```text
Garanta uma sessão GERID válida e informe somente o status da autenticação.
```

Depois teste:

```text
Liste minhas notificações do PAT.
Pesquise as tarefas atualizadas nos últimos 30 dias.
Detalhe a tarefa de protocolo 123456789.
Baixe o relatório consolidado do protocolo 123456789 e analise o texto.
```

Ferramentas disponibilizadas:

- `inss_gerid_ensure_session`;
- `inss_list_notifications`;
- `inss_search_tasks`;
- `inss_get_task`;
- `inss_download_attachment`;
- `inss_download_process`.

Downloads são gravados em `downloaded_files/inss`. PDFs com texto nativo são
convertidos localmente para Markdown e devolvidos ao Claude. O arquivo não é
enviado a um serviço externo pelo parser.

## 10. Atualizar o MCP

1. encerre o Claude Desktop;
2. faça backup de `.env`;
3. preserve o diretório configurado em `INSS_BROWSER_PROFILE`;
4. substitua os arquivos do MCP pela nova versão;
5. execute `npm install` novamente;
6. execute `npm test`;
7. reabra o Claude Desktop.

Manter o perfil em `~/Library/Application Support/Jurisync` evita perder a
sessão ao substituir a pasta do projeto ou descompactar uma atualização.

## Solução de problemas

### `zsh: command not found: claude`

O Claude Code CLI não está instalado, mas ele não é necessário. Configure o
servidor no Claude Desktop conforme a seção 8.

### `node: command not found` ou MCP desconectado

Execute `which node` no Terminal e use exatamente esse caminho em `command` no
JSON. Depois encerre o Claude Desktop com `Cmd+Q` e abra novamente.

### `Cannot find package` ou `Cannot find module`

Na raiz do projeto, execute:

```bash
npm install
```

Depois de atualizações, isso também instala o parser
`@firecrawl/pdf-inspector-wasm`.

### `.venv/bin/python` não existe

Execute novamente:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-inss.txt
```

### QR não reconhecido ou conta errada

Confirme que a imagem é um QR de exportação TOTP/Authenticator legível e que
`INSS_GERID_TOTP_ACCOUNT` corresponde à posição da conta desejada. Não use uma
captura cortada ou com baixa resolução.

### Certificado não aparece

- confirme que o A3 está conectado;
- abra o aplicativo do middleware e confirme que ele reconhece o certificado;
- verifique validade e cadeia do certificado;
- reinicie Chrome e Claude Desktop depois de instalar o middleware;
- tente o login GERID manualmente no Chrome para separar problema de
  middleware de problema de automação;
- verifique `chrome://policy` e a política de seleção automática.

### O PIN continua sendo solicitado

Isso é esperado. A janela é nativa e não pode ser preenchida pelo Playwright.
Alguns middlewares mantêm o PIN em memória por um período; essa política não é
controlada pelo MCP.

### `Chrome exited before its CDP endpoint became available`

Possíveis causas:

- o mesmo perfil dedicado já está aberto;
- a porta CDP está ocupada;
- o caminho do Chrome está errado;
- o perfil não tem permissão de escrita;
- uma execução anterior terminou de forma abrupta.

Verifique a porta:

```bash
lsof -nP -iTCP:9338 -sTCP:LISTEN
```

Feche a janela do Chrome de automação e tente novamente. Se outra aplicação
usar a porta, configure `INSS_CDP_PORT` com outro número.

### `Target page, context or browser has been closed`

Não feche a janela do Chrome durante o login, a escolha do certificado ou o
PIN. Execute novamente e aguarde o resultado no Terminal.

### A sessão abre em login mesmo estando autenticada

Confirme que todas as execuções usam o mesmo `INSS_BROWSER_PROFILE`. O MCP
valida a expiração do JWT e reabre o fluxo visível quando faltam menos de 30
segundos para expirar.

### Travado na seleção de abrangência, papel ou confirmação A3

Atualize `scripts/inss_gerid_playwright.mjs`. Se o campo de papel continuar
sem opções, confirme com o administrador do GERID se o usuário possui papel e
abrangência válidos para o PAT.

### Erro `dataAtualizacaoInicial ... tipo de dado inválido`

Atualize `integrations/inss/client.mjs`. A versão atual sempre envia datas ISO
válidas e usa uma janela padrão de 180 dias.

### Erro de `image.source.media_type` ao baixar PDF

Atualize `integrations/inss/client.mjs`, `scripts/inss_mcp_server.mjs`,
`package.json` e `package-lock.json`, execute `npm install` e reinicie o Claude.
A versão atual devolve Markdown extraído, não um PDF disfarçado de bloco de
imagem.

### PDF sem texto ou `pagesNeedingOcr` preenchido

O parser atual extrai texto nativo e identifica páginas digitalizadas, mas não
executa OCR. O PDF continua salvo localmente. OCR seletivo deve ser adicionado
somente quando documentos reais demonstrarem essa necessidade.

### O MCP não aparece no Claude

- valide o JSON em um validador;
- confirme todos os caminhos absolutos;
- confirme que `npm install` terminou sem erro;
- reinicie com `Cmd+Q`;
- consulte os logs nas configurações de desenvolvedor do Claude Desktop;
- em ambientes corporativos, confirme que MCP local não foi bloqueado por
  política administrativa.

## Segurança e operação

- mantenha QR, `.env`, perfil do Chrome e PDFs fora do Git;
- restrinja permissões desses arquivos ao usuário local;
- nunca registre PIN, segredo TOTP, `ifs_auth`, cookies ou bearer token em logs;
- não use `--show-secrets` em produção;
- não compartilhe o perfil persistente entre usuários;
- não use o perfil pessoal do Chrome;
- remova o A3 quando a máquina ficar desacompanhada;
- revogue credenciais e substitua o QR se houver suspeita de vazamento;
- mantenha Chrome, middleware, Claude Desktop, Node.js e macOS atualizados.

## Checklist final

- [ ] A3 conectado e reconhecido pelo middleware;
- [ ] certificado válido e PIN disponível ao titular;
- [ ] Chrome e Claude Desktop instalados;
- [ ] Node.js 22+, npm e Python 3 funcionando;
- [ ] `npm install` concluído;
- [ ] `.venv` criada e `requirements-inss.txt` instalado;
- [ ] QR protegido e `.env` configurado;
- [ ] perfil dedicado fora da pasta do projeto;
- [ ] primeiro login retornou `authenticated: true`;
- [ ] teste headless reutilizou a sessão;
- [ ] JSON do Claude usa caminhos absolutos;
- [ ] Claude Desktop foi reiniciado com `Cmd+Q`;
- [ ] `inss-gerid` aparece em Conectores;
- [ ] notificações, busca, detalhe e relatório foram testados.

## Referências

- Claude Desktop — MCP local e extensões:
  <https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop>
- Node.js:
  <https://nodejs.org/en/download>
- Mudança do Chrome 136 para depuração remota:
  <https://developer.chrome.com/blog/remote-debugging-port>
