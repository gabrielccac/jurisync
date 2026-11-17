# Instalação do Jurisync na máquina da Dra. Hérica

## Antes (nós)

- Envie `herica.jurisync` para a pasta Downloads dela. A senha do pacote vai por outro canal (WhatsApp, ligação).
- Dê à conta GitHub dela acesso de leitura ao repositório `gabrielccac/jurisync` (privado).
- O programa vem da `main`. O pacote foi gerado em 17/11/2026 com
  `jurisync-setup export --customer herica --portals <49 portais> --out customers/herica/instalacao/herica.jurisync`.
  Ficaram de fora TRF6 1G/2G (falta usuário e senha) e TJTO 1G/2G (o tribunal não publica o endereço de login por
  certificado).

Tribunais no pacote (49 logins; validação completa em 17/11, ver `../VALIDACAO-2026-11-17.md`):
- **PJe (27):** TRF1, TRF3, TRF5 (1º e 2º grau); TJBA, TJCE (1º e 2º), TJDFT, TJES (1º e 2º), TJMA, TJMT (1º e 2º),
  TJPA, TJPB, TJPE, TJAP, TJRN (1º e 2º), TJRO (1º e 2º), TJPI (1º e 2º).
- **Eproc (19):** TRF2 (RJ, ES, 2º grau), TRF4 (RS, SC, PR, 2º grau), TJSP (1º e 2º), TJSC (1º e 2º), TJRJ, TJRS,
  TJMG, TJPR (1º e 2º), TJAC (1º e 2º), TNU.
- **Projudi:** TJGO. **e-SAJ:** TJSP, TJMS, TJAL, TJAM. **DJEN.**

## Texto para colar no Claude dela (aba Code, pasta Downloads)

```text
Preciso instalar o Jurisync, que acessa com o meu certificado A1 o PJe (TRF1, TRF3, TRF5, TJBA, TJCE, TJDFT, TJES,
TJMA, TJMT, TJPA, TJPB, TJPE, TJAP, TJRN, TJRO e TJPI), o Eproc (TRF2, TRF4, TJSP, TJSC, TJRJ, TJRS, TJMG, TJPR, TJAC
e TNU), o Projudi do TJGO, o e-SAJ (TJSP, TJMS, TJAL e TJAM) e o DJEN. Você é responsável por toda a instalação,
incluindo os programas que faltarem, seguindo as regras abaixo.

O arquivo herica.jurisync (credenciais criptografadas) está na minha pasta Downloads. Se não estiver lá, procure em
Downloads, Área de Trabalho e Documentos, e me pergunte qual usar se achar mais de um. Se não encontrar, me diga e
PARE.

REGRAS
- Siga EXATAMENTE a ordem abaixo. Pare no primeiro erro e me avise, mostrando a mensagem completa.
- Peça minha permissão antes de instalar qualquer programa; depois de eu permitir, instale você mesmo.
- Nunca exiba o conteúdo de ~/.config/jurisync/.env, da pasta ~/.config/jurisync/files nem do arquivo
  herica.jurisync.
- NÃO me peça a senha do pacote no chat e NÃO peça para eu colá-la aqui. Ela é digitada numa página que o próprio
  programa abre no navegador. O mesmo vale para a senha do GitHub e a do computador.
- Não rode nenhum login mais de uma vez: tentativas repetidas podem bloquear a conta.
- Use os comandos do meu sistema (no Windows, PowerShell). Se o PATH não enxergar um programa recém-instalado, use o
  caminho completo dele em vez de pedir para eu reabrir o terminal.

PASSOS

0. Detecte meu sistema (Windows ou macOS) e confirme que há internet.

1. Git: rode git --version. Se faltar, instale:
   Windows: winget install --id Git.Git -e
   macOS: rode xcode-select --install, me diga para clicar em Instalar na janela que abrir e aguarde eu confirmar
   que terminou.

2. Acesso ao GitHub (o repositório é privado): rode
   git ls-remote https://github.com/gabrielccac/jurisync main
   Se responder com um hash, siga para o passo 3. Se pedir login ou negar acesso:
   a) Instale o GitHub CLI se faltar (gh --version). Windows: winget install --id GitHub.cli -e ; macOS: brew
      install gh (se não houver brew, me avise e PARE).
   b) Me peça para rodar gh auth login no terminal (GitHub.com, HTTPS, login pelo navegador) e aguarde eu
      confirmar. Não rode esse login por mim.
   c) Rode gh auth setup-git e repita o git ls-remote. Se ainda negar acesso, me diga que preciso pedir acesso ao
      repositório e PARE.

3. uv: rode uv --version. Se faltar:
   Windows: ANTES de instalar, crie no meu usuário três variáveis de ambiente permanentes (o app do Claude desvia o
   que é gravado em AppData e o uv quebra lá dentro) e defina as três também na sessão atual:
     UV_PYTHON_INSTALL_DIR = %USERPROFILE%\.local\share\uv\python
     UV_TOOL_DIR = %USERPROFILE%\.local\share\uv\tools
     UV_CACHE_DIR = %USERPROFILE%\.local\share\uv\cache
   (por exemplo, [Environment]::SetEnvironmentVariable("UV_TOOL_DIR", "$env:USERPROFILE\.local\share\uv\tools", "User"))
   Depois instale: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   O uv fica em %USERPROFILE%\.local\bin\uv.exe. Rode também uv tool update-shell.
   macOS: curl -LsSf https://astral.sh/uv/install.sh | sh (fica em ~/.local/bin/uv).

4. Google Chrome: confira se está instalado (o TJES põe uma verificação antes do login que o Jurisync resolve com o
   Chrome, sem abrir janela). Se faltar: Windows: winget install --id Google.Chrome -e ; macOS: me peça para
   instalar pelo site google.com/chrome e aguarde.

5. Claude Code (linha de comando): rode claude --version. Ele é necessário para registrar os servidores. Se faltar:
   Windows: irm https://claude.ai/install.ps1 | iex ; macOS: curl -fsSL https://claude.ai/install.sh | bash

6. Instale o Jurisync (o uv baixa o Python 3.12 e as dependências sozinho; leva alguns minutos):
   uv tool install --force --python 3.12 "git+https://github.com/gabrielccac/jurisync@main"
   Confirme que existem os comandos jurisync-setup, jurisync-pje, jurisync-eproc, jurisync-projudi, jurisync-esaj e
   jurisync-djen em ~/.local/bin (no Windows, %USERPROFILE%\.local\bin).

7. Rode: jurisync-setup import "<caminho completo do herica.jurisync que você encontrou>"
   - Ele abre uma página no navegador pedindo a "senha do pacote". Me diga para digitá-la NA PÁGINA e aguarde a
     conclusão. Se o navegador não abrir, mostre o endereço que ele imprimir (começa com http://127.0.0.1) para eu
     abrir.
   - Em seguida ele instala as credenciais e o certificado A1 em ~/.config/jurisync, testa o login de cada tribunal
     (49 logins, pode levar de 20 a 30 minutos; não interrompa) e registra os servidores pje, eproc, projudi, esaj
     e djen no Claude Code e no Claude Desktop.

8. Mostre apenas as linhas de resultado: "pronto" ou "FALHOU" de cada login e as de registro. Rode claude mcp list e
   mostre se pje, eproc, projudi, esaj e djen aparecem. Se algum login falhar, mostre a mensagem completa daquele
   tribunal, NÃO repita o login e siga para o passo 9 (os demais continuam funcionando).

9. Ao terminar, me diga para FECHAR COMPLETAMENTE e reabrir o Claude. Explique que agora posso pedir, por exemplo,
   "quais intimações tenho pendentes no TJBA?", "consulte o processo <número> no PJe do TRF1" ou "o que chegou no
   e-SAJ do TJMS?", e que o Jurisync não protocola nem altera nada nos tribunais. Avise também que, no Eproc, abrir
   o documento de uma intimação ainda pendente pode contar como ciência e começar o prazo: o Claude avisa antes
   quando isso puder acontecer. Por fim, me pergunte se posso apagar o arquivo herica.jurisync e a mensagem que
   contém a senha do pacote.
```
