# Instalação na máquina da Elisandra (sem plugin)

Antes de enviar:

- Gere o pacote: `uv run jurisync-setup export --customer elisandra --out ~/elisandra.jurisync`.
  Confira a linha "vindas do .env da raiz": essas variáveis precisam ser dela.
- Dê à conta GitHub dela acesso de leitura ao repositório `gabrielccac/jurisync` (privado).
- Envie o arquivo `.jurisync` e a senha do pacote por canais diferentes.

Ela cola o prompt abaixo no Claude (Claude Code ou aba Code do Claude Desktop).

No macOS:

- O Mac precisa ser Apple Silicon (`uname -m` = `arm64`). Num Mac Intel o `cryptography==50.0.2`
  não tem wheel pronto e a instalação tenta compilar.
- Se o macOS pedir para instalar o Rosetta 2, pode aceitar (é da Apple). Anote em que passo apareceu.
- Downloads, Mesa e Documentos são protegidos: na primeira leitura o macOS pergunta se o Claude pode
  acessar a pasta; clique em Permitir. Se já foi negado: Ajustes do Sistema → Privacidade e
  Segurança → Arquivos e Pastas → Claude (ou Terminal, se o Claude Code roda no Terminal).

```text
Preciso instalar o Jurisync, que acessa os portais do eproc (TRF4 1º grau, seções SC e RS, e TJRS)
e o DJEN, e a minha skill de intimações. Você é responsável por toda a instalação, incluindo os
programas que faltarem, seguindo as regras abaixo.

O arquivo de credenciais criptografadas se chama elisandra.jurisync e está na minha pasta Downloads.
Se não estiver lá, procure por ele nas pastas Downloads, Área de Trabalho e Documentos, e me
pergunte qual usar se achar mais de um. Se não encontrar, me diga e PARE.

REGRAS
- Siga EXATAMENTE a ordem abaixo. Pare no primeiro erro e me avise, mostrando a mensagem completa.
- Peça minha permissão antes de instalar qualquer programa; depois de eu permitir, instale você mesmo.
- Nunca exiba o conteúdo de ~/.config/jurisync/.env, da pasta ~/.config/jurisync/files nem do
  arquivo elisandra.jurisync.
- NÃO me peça a senha do pacote no chat e NÃO peça para eu colá-la aqui. Ela é digitada numa página
  que o próprio programa abre no navegador. O mesmo vale para a senha do GitHub.
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

4. Google Chrome: confirme que está instalado. O eproc faz login pelo Chrome e os anexos em HTML
   viram PDF por ele. Se faltar, instale: Windows: winget install --id Google.Chrome -e;
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
   uv tool install --force "git+https://github.com/gabrielccac/jurisync@feat/tjrs-eproc-setup"
   Confirme que existem os comandos jurisync-setup, jurisync-eproc e jurisync-djen (ficam em
   ~/.local/bin; no Windows, %USERPROFILE%\.local\bin).

7. Rode: jurisync-setup import "<caminho completo do elisandra.jurisync que você encontrou>"
   - Ele abre uma página no navegador pedindo a "senha do pacote". Me diga para digitá-la NA PÁGINA
     e aguarde a conclusão. Se o navegador não abrir, mostre o endereço que ele imprimir (começa
     com http://127.0.0.1) para eu abrir.
   - Em seguida ele instala as credenciais em ~/.config/jurisync, testa o login de cada portal do
     eproc (TRF4 SC, TRF4 RS e TJRS) e registra os servidores no Claude Code e no Claude Desktop.
     No primeiro login ele baixa o driver do Chrome sozinho. Os logins são lentos: espere cada um.

8. No macOS e no Windows, acrescente ao final do arquivo ~/.config/jurisync/.env a linha
   JURISYNC_CHROME_PATH="<caminho do Chrome do passo 4>"
   (com aspas, porque o caminho tem espaços) sem ler nem exibir o restante do arquivo (só acrescente
   a linha). No Linux não é necessário.

9. Skill de intimações: rode
   jurisync-setup skills --customer elisandra
   Ele baixa o repositório em ~/jurisync-src, liga a skill em ~/.claude/skills (Claude Code) e
   gera Downloads/elisandra-sop-intimacoes.zip para o Cowork. Se ele parar por mudanças locais,
   mostre a mensagem e PARE. Depois me explique como enviar o zip no app do Claude
   (Configurações → Capacidades → Skills → Enviar skill); se os nomes do menu forem diferentes,
   me ajude a achar a opção de enviar skill.

10. Mostre apenas as linhas de resultado: "pronto" ou "FALHOU" de cada login e as de registro.
    Rode claude mcp list e mostre se eproc e djen aparecem. Se algum falhar, mostre a mensagem
    completa e PARE. Não repita logins.

11. Ao terminar com tudo "pronto", me diga para FECHAR COMPLETAMENTE e reabrir o Claude. Explique
    que, para usar, basta pedir "verifique minhas intimações de hoje" (a skill
    elisandra-sop-intimacoes busca no DJEN, detalha no eproc e me mostra rascunhos para aprovar).
    Explique também que posso pedir para o Claude ajustar a skill e que as mudanças ficam em
    ~/jurisync-src. Por fim, me pergunte se posso apagar o arquivo elisandra.jurisync e a mensagem
    que contém a senha do pacote.
```

## Atualização

Depois que uma correção for enviada (push) para `feat/tjrs-eproc-setup`, ela cola no Claude:

```text
Atualize o Jurisync da minha máquina. Pare no primeiro erro e me mostre a mensagem completa.
1. Rode: uv tool install --force "git+https://github.com/gabrielccac/jurisync@feat/tjrs-eproc-setup"
2. Rode: jurisync-setup skills --customer elisandra
   Se ele parar porque mudei a skill localmente, me mostre a mensagem e PARE.
   Se ele disser "Reenvie no Cowork", me lembre de enviar o zip de Downloads no app do Claude.
3. Não rode jurisync-setup import de novo: as credenciais já estão instaladas.
4. Me diga para FECHAR COMPLETAMENTE e reabrir o Claude, para os servidores carregarem a versão nova.
```
