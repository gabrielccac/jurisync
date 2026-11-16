# Instalação na máquina da Cinthia (sem plugin)

| Sistema | Portais | Login |
|---|---|---|
| PJe | TRF1 1º grau, TRF3 1º grau, TRF5 1º grau/JEF | Token A3 + autenticador (`PJE_AUTH=a3`) |
| Eproc | TRF6 1º e 2º grau, TJMG 1º grau | Usuário, senha e autenticador (pelo Chrome) |
| DJEN | Consulta pública | Sem credencial |
| ADVBOX | Conta do escritório (a mesma do João), só leitura | E-mail, senha e dispositivo |

O login A3 do PJe foi validado ao vivo no TRF1 1G/2G e no TJAP. No TRF3 e no TRF5 ele é testado pela primeira vez
durante esta instalação: se falhar ali, os outros portais seguem funcionando.

## Antes de enviar

1. O PIN do token está em `customers/cinthia/.env` (`PJE_A3_PIN`), junto com `PJE_AUTH=a3`.
2. Gere o pacote. A senha vai para um arquivo, não para a tela:

   ```bash
   uv run jurisync-setup export --customer cinthia --out ~/cinthia.jurisync > ~/cinthia-pacote.txt
   ```

   A última linha de `~/cinthia-pacote.txt` é a senha do pacote. Confira também: o `PJE_AUTH` e o `PJE_A3_PIN`
   entre as variáveis, as verificações `pje:trf1-1g`, `pje:trf5-1g`, `pje:trf3-1g`, `eproc:trf6-1g`,
   `eproc:trf6-2g`, `eproc:tjmg-1g`, `advbox` e os servidores `advbox, djen, eproc, pje`.
3. Dê à conta GitHub dela acesso de leitura ao repositório `gabrielccac/jurisync` (privado).
4. Envie `cinthia.jurisync` (para Downloads) e a senha do pacote por OUTRO canal. Depois apague
   `~/cinthia-pacote.txt`.
5. O token A3 precisa estar plugado no computador em que ela vai usar o Claude, com o programa do token
   (driver) instalado. Ninguém deve usar o token nem entrar nesses portais durante a instalação.

No macOS:

- O Mac precisa ser Apple Silicon (`uname -m` = `arm64`).
- Downloads, Mesa e Documentos são protegidos: na primeira leitura o macOS pergunta se o Claude pode acessar a
  pasta; clique em Permitir.

```text
Preciso instalar o Jurisync, que acessa o PJe do TRF1, do TRF3 e do TRF5 com o meu token A3, o eproc do
TRF6 (1º e 2º grau) e do TJMG, o DJEN e o ADVBOX do escritório. Você é responsável por toda a instalação, incluindo os programas que
faltarem, seguindo as regras abaixo.

O arquivo de credenciais criptografadas se chama cinthia.jurisync e está na minha pasta Downloads.
Se não estiver lá, procure por ele nas pastas Downloads, Área de Trabalho e Documentos, e me
pergunte qual usar se achar mais de um. Se não encontrar, me diga e PARE.

REGRAS
- Siga EXATAMENTE a ordem abaixo. Pare no primeiro erro e me avise, mostrando a mensagem completa.
- Eu já autorizo você a instalar todos os programas listados nos passos abaixo (Git, GitHub CLI, uv,
  Google Chrome, Claude Code e o Jurisync): instale direto, sem me perguntar antes. Só me chame quando
  um passo exigir uma ação minha (clicar numa janela, digitar uma senha, plugar o token).
- Nunca exiba o conteúdo de ~/.config/jurisync/.env, da pasta ~/.config/jurisync/files nem do
  arquivo cinthia.jurisync.
- NÃO me peça a senha do pacote nem o PIN do token no chat. A senha do pacote é digitada numa página
  que o próprio programa abre no navegador; o PIN já vem dentro do pacote. O mesmo vale para a senha do
  GitHub e a do computador.
- Não rode nenhum login mais de uma vez: tentativas repetidas podem bloquear a conta, e um PIN errado
  repetido bloqueia o token.
- O eproc recusa um segundo login da mesma conta: não faça logins em paralelo.
- Use os comandos do meu sistema (no Windows, PowerShell). Se o PATH não enxergar um programa recém-
  instalado, use o caminho completo dele em vez de pedir para eu reabrir o terminal.

PASSOS

0. Detecte meu sistema (macOS, Windows ou Linux) e confirme que há internet.

1. Git: rode git --version. Se faltar, instale pelo método oficial do sistema:
   macOS: rode xcode-select --install. Ele abre uma janela das Ferramentas de Linha de Comando: me
   diga para clicar em Instalar, aceitar a licença e me avisar quando terminar (leva de 5 a 20
   minutos). Não siga antes disso. Depois confirme com xcode-select -p e git --version. Se disser
   que já está instalado mas o git não funcionar, me peça para rodar sudo xcode-select --reset no
   Terminal (ele pede a senha do Mac, que eu digito lá, não aqui);
   Windows: winget install --id Git.Git -e;
   Linux: o gerenciador de pacotes da distribuição.

2. Acesso ao GitHub (o repositório é privado): rode
   git ls-remote https://github.com/gabrielccac/jurisync HEAD
   Se responder com um hash, siga para o passo 3. Se pedir login ou negar acesso:
   a) Instale o GitHub CLI se faltar (gh --version): Windows: winget install --id GitHub.cli -e;
      macOS: brew install gh (se não houver Homebrew, me diga para baixar em https://cli.github.com
      e PARE); Linux: o gerenciador de pacotes.
   b) Me peça para rodar gh auth login no Terminal (GitHub.com, HTTPS, login pelo navegador) e
      aguarde eu confirmar. Não rode esse login por mim.
   c) Rode gh auth setup-git e repita o git ls-remote. Se ainda negar acesso, me diga que preciso
      pedir acesso ao repositório e PARE.

3. uv: rode uv --version. Se faltar, instale pelo método oficial:
   macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh
   Windows: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   O uv fica em ~/.local/bin/uv (Windows: %USERPROFILE%\.local\bin\uv.exe). Rode também
   uv tool update-shell para os próximos terminais.
   No Windows, ANTES de instalar qualquer coisa com o uv, crie no meu usuário três variáveis de
   ambiente permanentes (o app do Claude desvia o que é gravado em AppData e o uv quebra lá dentro):
   UV_PYTHON_INSTALL_DIR = %USERPROFILE%\.local\share\uv\python
   UV_TOOL_DIR = %USERPROFILE%\.local\share\uv\tools
   UV_CACHE_DIR = %USERPROFILE%\.local\share\uv\cache
   (por exemplo, [Environment]::SetEnvironmentVariable("UV_TOOL_DIR", "$env:USERPROFILE\.local\share\uv\tools", "User"))
   e defina as três também na sessão atual do PowerShell.

4. Google Chrome: confirme que está instalado. O eproc do TRF6 e do TJMG faz login pelo Chrome e os
   anexos em HTML viram PDF por ele. Se faltar, instale: Windows: winget install --id Google.Chrome -e;
   macOS: me diga para baixar em https://www.google.com/chrome e aguarde; Linux: o pacote oficial
   google-chrome-stable. Anote o caminho do executável:
   macOS: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
   Windows: C:\Program Files\Google\Chrome\Application\chrome.exe
   Linux: o resultado de which google-chrome

5. Token A3: me peça para plugar o token e confirmar que o programa dele está instalado. Ache a
   biblioteca PKCS#11 do token e anote o caminho completo:
   - SafeSign / G&D StarSign (o padrão): macOS /Applications/tokenadmin.app/Contents/Frameworks/libaetpkss.dylib;
     Windows C:\Windows\System32\aetpkss1.dll; Linux /usr/lib/libaetpkss.so.
   - SafeNet eToken: macOS /usr/local/lib/libeTPkcs11.dylib; Windows C:\Windows\System32\eTPKCS11.dll;
     Linux /usr/lib/libeTPkcs11.so.
   - Outro fabricante: procure a biblioteca PKCS#11 instalada pelo programa do token (.dylib, .dll ou .so)
     e me pergunte se não tiver certeza. Se não houver nenhuma, me diga para instalar o programa do
     token e PARE.

6. Claude Code (linha de comando): rode claude --version. Ele é necessário para registrar os
   servidores. Se faltar, instale pelo método oficial:
   macOS/Linux: curl -fsSL https://claude.ai/install.sh | bash
   Windows: irm https://claude.ai/install.ps1 | iex

7. Instale o Jurisync (o uv baixa o Python 3.12 e as dependências sozinho; leva alguns minutos):
   uv tool install --force --python 3.12 "git+https://github.com/gabrielccac/jurisync@main"
   Confirme que existem os comandos jurisync-setup, jurisync-pje, jurisync-eproc, jurisync-djen e jurisync-advbox
   (ficam em ~/.local/bin; no Windows, %USERPROFILE%\.local\bin).

8. Confira que o Jurisync enxerga o token, SEM usar o PIN: rode o Python do Jurisync
   (macOS/Linux: ~/.local/share/uv/tools/jurisync/bin/python;
   Windows: %USERPROFILE%\.local\share\uv\tools\jurisync\Scripts\python.exe) com
   -m jurisync.connectors.pje.auth.a3 check
   e a variável de ambiente PJE_A3_MODULE com o caminho do passo 5. Ele deve mostrar o token e o
   certificado. Não use a opção --sign. Se não achar o token, mostre a mensagem completa e PARE.

9. Rode, com a mesma variável PJE_A3_MODULE do passo 8 definida no comando:
   jurisync-setup import "<caminho completo do cinthia.jurisync que você encontrou>"
   - Ele abre uma página no navegador pedindo a "senha do pacote". Me diga para digitá-la NA PÁGINA
     e aguarde a conclusão. Se o navegador não abrir, mostre o endereço que ele imprimir (começa
     com http://127.0.0.1) para eu abrir.
   - Em seguida ele instala as credenciais em ~/.config/jurisync, testa o login do PJe (TRF1, TRF5 e
     TRF3, com o token), do eproc (TRF6 1º e 2º grau e TJMG) e do ADVBOX, e registra os servidores no Claude Code
     e no Claude Desktop. Entre um login e outro ele espera até 30 segundos por um código novo do
     autenticador: é normal. No primeiro login do eproc ele baixa o driver do Chrome sozinho. Os logins
     são lentos: espere cada um. Não tire o token durante os testes.

10. Acrescente ao final do arquivo ~/.config/jurisync/.env, sem ler nem exibir o restante (só acrescente):
    PJE_A3_MODULE="<caminho da biblioteca do passo 5>"
    e, no macOS e no Windows, também
    JURISYNC_CHROME_PATH="<caminho do Chrome do passo 4>"
    (com aspas, porque os caminhos podem ter espaços).

11. Mostre apenas as linhas de resultado: "pronto" ou "FALHOU" de cada login e as de registro.
    Rode claude mcp list e mostre se pje, eproc, djen e advbox aparecem. Se algum login falhar, mostre a
    mensagem completa e PARE. Não repita logins.

12. Ao terminar, me diga para FECHAR COMPLETAMENTE e reabrir o Claude, e que o token precisa estar
    plugado sempre que eu usar o PJe pelo Claude. Explique que, para usar, basta pedir coisas como
    "liste os expedientes pendentes do TRF1" ou "mostre o processo <número>". Por fim, me pergunte se
    posso apagar o arquivo cinthia.jurisync e a mensagem que contém a senha do pacote.
```

## Atualização

Depois que uma correção for enviada (push) para a `main`, ela cola no Claude:

```text
Atualize o Jurisync da minha máquina. Pare no primeiro erro e me mostre a mensagem completa.
1. Rode: uv tool install --force --python 3.12 "git+https://github.com/gabrielccac/jurisync@main"
2. Não rode jurisync-setup import de novo: as credenciais já estão instaladas.
3. Me diga para FECHAR COMPLETAMENTE e reabrir o Claude, para os servidores carregarem a versão nova.
```
