# Instalação na máquina do Wesley (sem plugin)

Antes de enviar:

- Gere o pacote: `uv run jurisync-setup export --customer wesley --out ~/wesley.jurisync` e anote a senha
  que aparece (só aparece nessa hora). Os servidores do pacote são `pje`, `eproc`, `juridiq` e `djen`; as
  verificações são PJe TRF1 1g/2g, TRF3 1g e TRF5 1g, Eproc TRF4 2g/SC/RS/PR, Eproc TRF6 1g/2g e Juridiq.
- O pacote leva o login do TRF6 que estava no `.env` dele na hora da exportação (usuário + senha + 2FA). Confira
  se é o definitivo antes de enviar.
- Dê à conta GitHub dele acesso de leitura ao repositório `gabrielccac/jurisync` (privado).
- Envie `wesley.jurisync` (para Downloads) e a senha do pacote por OUTRO canal.
- Não há skill de intimações para o Wesley: nada de zip.

Ele cola o prompt abaixo no Claude (Claude Code ou aba Code do Claude Desktop).

No macOS:

- O Mac precisa ser Apple Silicon (`uname -m` = `arm64`). Num Mac Intel o `cryptography==50.0.2`
  não tem wheel pronto e a instalação tenta compilar.
- Downloads, Mesa e Documentos são protegidos: na primeira leitura o macOS pergunta se o Claude pode
  acessar a pasta; clique em Permitir.

```text
Preciso instalar o Jurisync, que acessa o PJe do TRF1 (1º e 2º grau), TRF3 (1º grau) e TRF5 (1º grau/JEF),
o eproc do TRF4 (2º grau e 1º grau SC, RS e PR) e do TRF6 (1º e 2º grau), o Juridiq e o DJEN. Você é
responsável por toda a instalação, incluindo os programas que faltarem, seguindo as regras abaixo.

O arquivo de credenciais criptografadas se chama wesley.jurisync e está na minha pasta Downloads.
Se não estiver lá, procure por ele nas pastas Downloads, Área de Trabalho e Documentos, e me
pergunte qual usar se achar mais de um. Se não encontrar, me diga e PARE.

REGRAS
- Siga EXATAMENTE a ordem abaixo. Pare no primeiro erro e me avise, mostrando a mensagem completa.
- Peça minha permissão antes de instalar qualquer programa; depois de eu permitir, instale você mesmo.
- Nunca exiba o conteúdo de ~/.config/jurisync/.env, da pasta ~/.config/jurisync/files nem do
  arquivo wesley.jurisync.
- NÃO me peça a senha do pacote no chat e NÃO peça para eu colá-la aqui. Ela é digitada numa página
  que o próprio programa abre no navegador. O mesmo vale para a senha do GitHub e a do computador.
- Não rode nenhum login mais de uma vez: tentativas repetidas podem bloquear a conta.
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

4. Google Chrome: confirme que está instalado. O eproc do TRF6 faz login pelo Chrome e os anexos em
   HTML viram PDF por ele. Se faltar, instale: Windows: winget install --id Google.Chrome -e;
   macOS: me diga para baixar em https://www.google.com/chrome e aguarde; Linux: o pacote oficial
   google-chrome-stable. Anote o caminho do executável:
   macOS: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
   Windows: C:\Program Files\Google\Chrome\Application\chrome.exe
   Linux: o resultado de which google-chrome

5. Claude Code (linha de comando): rode claude --version. Ele é necessário para registrar os
   servidores. Se faltar, instale pelo método oficial:
   macOS/Linux: curl -fsSL https://claude.ai/install.sh | bash
   Windows: irm https://claude.ai/install.ps1 | iex

6. Instale o Jurisync (o uv baixa o Python 3.12 e as dependências sozinho; leva alguns minutos):
   uv tool install --force --python 3.12 "git+https://github.com/gabrielccac/jurisync@feat/tjrs-eproc-setup"
   Confirme que existem os comandos jurisync-setup, jurisync-pje, jurisync-eproc, jurisync-juridiq e
   jurisync-djen (ficam em ~/.local/bin; no Windows, %USERPROFILE%\.local\bin).

7. Rode: jurisync-setup import "<caminho completo do wesley.jurisync que você encontrou>"
   - Ele abre uma página no navegador pedindo a "senha do pacote". Me diga para digitá-la NA PÁGINA
     e aguarde a conclusão. Se o navegador não abrir, mostre o endereço que ele imprimir (começa
     com http://127.0.0.1) para eu abrir.
   - Em seguida ele instala as credenciais e o certificado A1 em ~/.config/jurisync, testa o login
     do PJe (TRF1 1º e 2º grau, TRF3 1º grau, TRF5 1º grau), do eproc (TRF4 2º grau, SC, RS e PR;
     TRF6 1º e 2º grau) e do Juridiq e registra os servidores no Claude Code e no Claude Desktop.
     No primeiro login do TRF6 ele baixa o driver do Chrome sozinho. Os logins são lentos: espere
     cada um. O TRF6 às vezes demora por causa de uma verificação anti-robô; espere até o fim.

8. No macOS e no Windows, acrescente ao final do arquivo ~/.config/jurisync/.env a linha
   JURISYNC_CHROME_PATH="<caminho do Chrome do passo 4>"
   (com aspas, porque o caminho tem espaços) sem ler nem exibir o restante do arquivo (só acrescente
   a linha). No Linux não é necessário.

9. Mostre apenas as linhas de resultado: "pronto" ou "FALHOU" de cada login e as de registro.
   Rode claude mcp list e mostre se pje, eproc, juridiq e djen aparecem. Se algum falhar, mostre a
   mensagem completa e PARE. Não repita logins.

10. Ao terminar com tudo "pronto", me diga para FECHAR COMPLETAMENTE e reabrir o Claude. Explique
    que, para usar, basta pedir coisas como "liste as intimações pendentes do TRF1" ou "mostre o
    processo <número>". Por fim, me pergunte se posso apagar o arquivo wesley.jurisync e a
    mensagem que contém a senha do pacote.
```

## Atualização

Depois que uma correção for enviada (push) para `feat/tjrs-eproc-setup`, ele cola no Claude:

```text
Atualize o Jurisync da minha máquina. Pare no primeiro erro e me mostre a mensagem completa.
1. Rode: uv tool install --force --python 3.12 "git+https://github.com/gabrielccac/jurisync@feat/tjrs-eproc-setup"
2. Não rode jurisync-setup import de novo: as credenciais já estão instaladas.
3. Me diga para FECHAR COMPLETAMENTE e reabrir o Claude, para os servidores carregarem a versão nova.
```
