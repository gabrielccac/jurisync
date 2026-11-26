# Guia: conectar cada escritório ao Jurisync remoto

Um servidor atende todos os escritórios. Cada escritório adiciona o conector **Jurisync Tribunais**
(`https://<domínio>/tribunais/mcp`: todos os tribunais dele e o DJEN) e, se usa ADVBOX ou Juridiq, um conector para
cada um (`https://<domínio>/advbox/mcp`, `https://<domínio>/juridiq/mcp`). Cada advogado entra com o próprio e-mail e
senha; o servidor mostra só os portais e sistemas daquele escritório. O endereço antigo `https://<domínio>/mcp` (tudo
num conector só) continua funcionando para quem já usa (Victor), mas está descontinuado: escritório novo recebe os
endereços acima. Nada é instalado no computador do escritório.
Exceção: escritórios com token A3 (barbara, cinthia) continuam na instalação local (`settings.toml` →
`[hosted] mode = "local"`).

Parte A é para nós (operador). Parte B é o texto para mandar ao escritório.

---

## Parte A — Operador

### A1. Antes do primeiro escritório (uma vez)

Servidor no ar conforme `deploy/README.md` §1 (`push.sh`, `set-secret.sh` com `JURISYNC_CAPTCHA_API_KEY` e a chave do
cofre guardada no gerenciador de senhas). Conferir:

```bash
curl -s https://<domínio>/healthz                     # {"ok": true}
/opt/jurisync/app/.venv/bin/python /opt/jurisync/app/deploy/smoke_test.py https://<domínio>
```

### A2. Preparar o escritório (na nossa máquina)

1. `customers/<nome>/portals.toml` com os portais validados e `[env].set` (certificado, perfil da OAB, conta do
   autenticador); `customers/<nome>/.env` com as credenciais. Portal que falhou na validação sai do `portals.toml`
   (senão é oferecido sem funcionar).
2. Exportar e ler o plano:

   ```bash
   uv run jurisync-hosted provision export --out ~/rollout --passphrase-file ~/.config/jurisync/rollout.passphrase \
     --customers-dir ~/orca/jurisync/customers --customer <nome>
   ```

   A saída lista, por escritório, os portais que vão para o servidor e os que ficam de fora com o motivo (A3, segredo
   faltando, portal não atendido), e os endereços que o escritório vai adicionar (`endpoints` no `plan.json`). Corrigir o que for erro nosso e exportar de novo.
3. Em `~/rollout/plan.json`, pôr os e-mails dos advogados na lista `users` do escritório e exportar outra vez (os
   usuários são mantidos).

### A3. Instalar no servidor

```bash
scp -r ~/rollout <vps>:/tmp/rollout
ssh <vps> 'sudo install -m 600 -o jurisync /dev/stdin /var/lib/jurisync/rollout.passphrase' < ~/.config/jurisync/rollout.passphrase
ssh <vps> 'sudo rsync -a --chown=jurisync:jurisync /tmp/rollout/ /var/lib/jurisync/rollout/ && rm -rf /tmp/rollout'
ssh -t <vps> sudo jurisync-admin provision apply /var/lib/jurisync/rollout \
  --passphrase-file /var/lib/jurisync/rollout.passphrase --office <nome>
ssh <vps> 'sudo shred -u /var/lib/jurisync/rollout.passphrase'
```

`apply` cria ou atualiza o escritório, troca o conteúdo do cofre pelo do pacote (portais retirados saem; o
`ADVBOX_DEVICE_ID` renovado no servidor é mantido) e cria os usuários com senha aleatória em
um arquivo novo por execução, `/var/lib/jurisync/hosted/new-user-passwords-<data>-<id>.txt`. O advogado troca essa
senha no primeiro login. Nenhum login em portal acontece aqui.

### A4. Provar os logins no servidor

```bash
sudo /opt/jurisync/app/deploy/check-office.sh <nome>     # um login por portal, 30 s entre eles, para na 1ª falha
sudo jurisync-admin portal list <nome>                   # estado de cada portal
```

`portal list` mostra uma linha por portal: sistema, método (certificado A1 / senha), se tem autenticador e o último
teste (`OK`, `falhou: [motivo] …` ou `não testado`). Um portal declarado mas sem login no cofre aparece como
`não oferecido: falta login no cofre`. O check usa exatamente a configuração que o servidor usa nas chamadas.

Motivos de falha e o que fazer:

| Motivo | O que fazer |
|---|---|
| `credentials_refused` | senha errada: pedir a senha atual ao escritório |
| `password_expired` | o advogado troca a senha no portal e nos manda a nova |
| `otp_refused` | conta errada do autenticador (`*_OTP_PROFILE_MATCH`/`INDEX`) ou QR antigo: pedir QR novo |
| `certificate_refused` / `not_registered` | certificado não vinculado ou sem cadastro naquele tribunal: o escritório resolve no tribunal; tirar o portal do `portals.toml` até lá |
| `captcha` | CAPTCHA não resolvido (TJES, TJTO, Projudi TJBA): tentar de novo mais tarde |
| `blocked` | conta bloqueada no portal: o escritório desbloqueia |
| `portal_unavailable` | tribunal fora do ar: tentar mais tarde |
| `login_missing` | falta credencial no cofre (a mensagem diz qual variável) |

Uma recusa bloqueia novas tentativas daquele portal por 15 min. Trocar a credencial no cofre libera na hora, sem
reiniciar o servidor.

### A5. Entregar o acesso

1. Mandar a cada advogado a Parte B, com os endereços do escritório (`endpoints` do `plan.json`: sempre
   `https://<domínio>/tribunais/mcp`; mais `/advbox/mcp` ou `/juridiq/mcp` se ele usa), o e-mail dele e a senha (a
   senha por outro canal: WhatsApp separado, ligação). Na Parte B, apagar o passo do ADVBOX/Juridiq se o escritório
   não usa. Depois apagar o `new-user-passwords-*.txt`.
2. Acompanhar o primeiro uso: `sudo jurisync-admin calls <nome> --limit 20`.

### A6. Manutenção

| Situação | Comando |
|---|---|
| Senha, QR ou certificado novo | atualizar `customers/<nome>/.env` e repetir A2–A4 só para esse escritório |
| Tirar um portal | `sudo jurisync-admin portal remove <nome> <portal>` (e tirar do `portals.toml`) |
| Certificado revogado | `sudo jurisync-admin certificate remove <nome>` |
| Advogado novo | `sudo jurisync-admin user add <email> <nome>` |
| Advogado saiu | `sudo jurisync-admin user remove <email>` (encerra os tokens dele) |
| Trocar senha de um advogado | `sudo jurisync-admin user password <email>` |
| Ver uso e alterações | `jurisync-admin calls <nome>` e `jurisync-admin history <nome>` |
| Escritório passa a ser só local (A3) | `[hosted] mode = "local"` no `settings.toml` e repetir A2–A3 |

Skills do escritório no Claude Code / Cowork apontando para o servidor:
`uv run jurisync-hosted provision plugin <pasta-do-plugin> --url https://<domínio> --out dist/<nome>-remote` (um servidor
`tribunais` e um por sistema de gestão que o plugin usa).

---

## Parte B — Para o escritório

> **Como conectar o Jurisync ao seu Claude**
>
> Você vai precisar de: os endereços abaixo, seu e-mail e a senha que enviamos.
>
> **No Claude (site claude.ai ou aplicativo do computador)**
> 1. Abra **Configurações → Conectores**.
> 2. Clique em **Adicionar conector personalizado**.
> 3. Nome: `Jurisync Tribunais`. URL: `https://<domínio>/tribunais/mcp`. Clique em **Adicionar**.
> 4. Clique em **Conectar**. Abre a página **Entrar no Jurisync**: digite seu e-mail e a senha e confirme.
> 5. Se o escritório usa ADVBOX, repita os passos 2 a 4 com o nome `Jurisync ADVBOX` e a URL
>    `https://<domínio>/advbox/mcp` (Juridiq: `Jurisync Juridiq`, `https://<domínio>/juridiq/mcp`).
> 6. Pronto. O conector Tribunais traz todos os tribunais do escritório (PJe, Eproc, e-SAJ, Projudi...) e o DJEN. Numa conversa, peça por exemplo: *"Liste minhas intimações pendentes no PJe do TRF1"* ou
>    *"Abra o processo 0000000-00.0000.0.00.0000 e resuma a última decisão"*.
>
> Em planos Team/Enterprise, o administrador da conta pode adicionar o conector para todos (Configurações da
> organização → Conectores); cada advogado ainda faz o passo 4 (em cada conector) com o próprio e-mail.
>
> O conector também aparece no aplicativo do celular depois de configurado na conta.
>
> **Ciência das intimações**
> Ao ler uma intimação pendente no PJe, no Projudi ou no JPe, o Claude avisa antes que a leitura pode registrar a
> ciência e pergunta se pode seguir. No Eproc o portal registra a ciência ao abrir o documento: o Claude informa
> depois. No e-SAJ, no Tucujuris e no STJ ele não abre intimação pendente (a ciência fica para o portal). Se quiser que ele leia e dê ciência sem perguntar (por exemplo numa rotina diária), diga isso na
> instrução: *"pode dar ciência nas leituras"*. Ele sempre informa quais intimações tiveram ciência.
>
> **ADVBOX pedindo código**
> Se o ADVBOX pedir verificação do dispositivo, o Claude mostra para qual e-mail o código foi enviado e abre um campo
> para você digitar. Se o campo não aparecer, responda na conversa com o código recebido.
>
> **Se algo falhar**
> A mensagem do Claude diz o motivo (por exemplo, senha expirada no portal ou falta de cadastro no tribunal). Envie
> para nós o print da mensagem. Sua senha do Jurisync nunca deve ser enviada por e-mail ou mensagem.
