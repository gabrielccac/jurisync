# Instalação na máquina do Victor (sem plugin)

Antes de enviar:

- Gere o pacote: `uv run jurisync-setup export --customer victor --out ~/victor.jurisync` e anote a senha
  que aparece (só aparece nessa hora). Os servidores do pacote são `pje`, `advbox` e `djen`; as verificações são PJe TRF5 1g/2g, TRF1 1g/2g, TJPE 1g/2g e ADVBOX.
- Dê à conta GitHub dele acesso de leitura ao repositório `gabrielccac/jurisync` (privado).
- Envie `victor.jurisync` e `victor-sop-intimacoes.zip` (para Downloads) e a senha do pacote por outro canal.
- `victor-pje-filing` fica fora por enquanto: o servidor `filing` ainda não está no GitHub.

```text
Preciso instalar o Jurisync, que acessa o PJe do TRF5 (1º grau/JEF e 2º grau/Turmas Recursais) e do
TRF1 e TJPE (1º e 2º grau), todos com o meu certificado A1, o ADVBOX do escritório e o DJEN, e a minha skill
de intimações. Você é responsável por toda a
instalação, incluindo os programas que faltarem, seguindo as regras abaixo.

Os arquivos victor.jurisync (credenciais criptografadas) e victor-sop-intimacoes.zip (skill) estão
na minha pasta Downloads. Se não estiverem lá, procure nas pastas Downloads, Área de Trabalho e
Documentos, e me pergunte qual usar se achar mais de um. Se não encontrar, me diga e PARE.

REGRAS
- Siga EXATAMENTE a ordem abaixo. Pare no primeiro erro e me avise, mostrando a mensagem completa.
- Peça minha permissão antes de instalar qualquer programa; depois de eu permitir, instale você mesmo.
- Nunca exiba o conteúdo de ~/.config/jurisync/.env, da pasta ~/.config/jurisync/files nem do
  arquivo victor.jurisync.
- NÃO me peça a senha do pacote no chat e NÃO peça para eu colá-la aqui. Ela é digitada numa página
  que o próprio programa abre no navegador. O mesmo vale para a senha do GitHub e a do computador.
- Não rode nenhum login mais de uma vez: tentativas repetidas podem bloquear a conta.
- Use os comandos do meu sistema (no Windows, PowerShell). Se o PATH não enxergar um programa recém-
  instalado, use o caminho completo dele em vez de pedir para eu reabrir o terminal.

PASSOS

0. Detecte meu sistema (macOS, Windows ou Linux) e confirme que há internet.

1. Git: rode git --version. Se faltar, instale:
   macOS: rode xcode-select --install. Ele abre uma janela das Ferramentas de Linha de Comando: me
   diga para clicar em Instalar, aceitar a licença e me avisar quando terminar (leva de 5 a 20
   minutos). Não siga antes disso. Depois confirme com git --version. Se disser que já está
   instalado mas o git não funcionar, me peça para rodar sudo xcode-select --reset no Terminal
   (ele pede a senha do Mac, que eu digito lá, não aqui);
   Windows: winget install --id Git.Git -e; Linux: o gerenciador de pacotes da distribuição.

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

4. Claude Code (linha de comando): rode claude --version. Ele é necessário para registrar os
   servidores. Se faltar, instale pelo método oficial:
   macOS/Linux: curl -fsSL https://claude.ai/install.sh | bash
   Windows: irm https://claude.ai/install.ps1 | iex

5. Instale o Jurisync (o uv baixa o Python 3.12 e as dependências sozinho; leva alguns minutos):
   uv tool install --force "git+https://github.com/gabrielccac/jurisync@feat/tjrs-eproc-setup"
   Confirme que existem os comandos jurisync-setup, jurisync-pje, jurisync-advbox e jurisync-djen
   (ficam em ~/.local/bin; no Windows, %USERPROFILE%\.local\bin).

6. Rode: jurisync-setup import "<caminho completo do victor.jurisync que você encontrou>"
   - Ele abre uma página no navegador pedindo a "senha do pacote". Me diga para digitá-la NA PÁGINA
     e aguarde a conclusão. Se o navegador não abrir, mostre o endereço que ele imprimir (começa
     com http://127.0.0.1) para eu abrir.
   - Em seguida ele instala as credenciais e o certificado A1 em ~/.config/jurisync, testa o login
     do PJe TRF5, TRF1 e TJPE (1º e 2º grau cada) e do ADVBOX e registra os servidores no Claude Code e no Claude Desktop. Pode levar
     alguns minutos.

7. Skill de intimações a partir do zip:
   - Claude Code: extraia o zip em ~/.claude/skills, de forma que exista
     ~/.claude/skills/victor-sop-intimacoes/SKILL.md. Se já houver uma pasta
     victor-sop-intimacoes ali, renomeie-a para victor-sop-intimacoes.bak antes.
     macOS/Linux: mkdir -p ~/.claude/skills && unzip -o "<caminho do zip>" -d ~/.claude/skills
     Windows: Expand-Archive -Path "<caminho do zip>" -DestinationPath "$env:USERPROFILE\.claude\skills" -Force
   - Cowork: me explique como enviar o mesmo zip no app do Claude (Configurações → Capacidades →
     Skills → Enviar skill); se os nomes do menu forem diferentes, me ajude a achar a opção.

8. Mostre apenas as linhas de resultado: "pronto" ou "FALHOU" de cada login e as de registro.
   Rode claude mcp list e mostre se pje, advbox e djen aparecem. Se algum falhar, mostre a mensagem
   completa e PARE. Não repita logins.

9. Ao terminar com tudo "pronto", me diga para FECHAR COMPLETAMENTE e reabrir o Claude. Explique que
   a skill victor-sop-intimacoes busca as intimações no DJEN, detalha no PJe e propõe as tarefas do
   ADVBOX, e que a criação de tarefas no ADVBOX está LIGADA nesta instalação: peça sempre a prévia
   antes de mandar criar. Por fim, me pergunte se posso apagar o arquivo victor.jurisync e a
   mensagem que contém a senha do pacote.
```
