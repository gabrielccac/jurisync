# Migração de todos os escritórios para o MCP remoto (plano, 16/11/2026)

Objetivo: cada escritório usa o Jurisync adicionando **uma URL** (`https://<host>/mcp`) no Claude ou no ChatGPT e
entrando uma vez. Sem instalação local, sem `.env` na máquina do escritório. O servidor é o modo hospedado que já
existe (`docs/hosted.md`): OAuth por escritório, cofre cifrado por escritório, pool de sessões por
(escritório, portal). Este plano diz em que ordem levar os 19 escritórios para lá e o que falta em cada um.
Complementa `docs/rollout-plan.md` (fases A–E), que segue valendo para cofre/página do escritório e cache/sync.

## Onde estamos

- **Victor** usa hoje uma instância dedicada só de PJe (conector "Pje Victor Hugo" no claude.ai, ferramentas
  `pje_*` sem escrita). Existe também "Pje Fabrício", no mesmo formato, hoje fora do ar (502). Nenhuma das duas
  roda nesta máquina: confirmar host, versão e quem administra antes da fase 2.
- O servidor hospedado já serve PJe, Eproc, e-SAJ, Projudi, STJ, DJEN, ADVBOX e Juridiq (`TENANT_READY`) numa URL
  só (`/mcp`), com escrita de PJe desligada e escrita de ADVBOX/Juridiq só para quem tiver `*_ALLOW_WRITES`.
- Credenciais entram pelo pacote cifrado (`jurisync-setup export` → `jurisync-hosted secrets import-bundle
  <escritório> <arquivo> --portal <chave> --check`), que só grava depois de um login que funciona. A página do
  escritório (cadastro self-service) tem a lógica pronta em `hosted/access.py`, mas a tela não existe.
- A validação de 16/11 deixou 70 portais com login + leituras validados (`docs/validation/*-2026-11-16.md`).
- Tudo isso está na branch local `integrate/main`, ainda sem push.

## O que cada forma de login exige no servidor

| Forma (`method`) | No servidor | Escritórios |
|---|---|---|
| `certificate_totp`, `certificate_mtls`, `certificate_challenge` (A1 + PIN, + TOTP) | Funciona por HTTP; o A1 fica em memória, vindo do cofre | allan, anaflavia, denis, fernanda, herica, patricia, victor, wesley (+ portais A1 de outros) |
| `password_totp` (usuário + senha + TOTP, PJe/SSO) | Funciona por HTTP | allan, eliane, jean, joao, kirlia, natalia, navarro, thaile |
| `browser_totp` (Eproc por senha no Chrome) | Precisa de Chrome headless no servidor (já é como o conector faz); limitar navegadores simultâneos pelo semáforo de logins | 14 escritórios têm ao menos um |
| A3 (token físico; `EPROC_AUTH=a3`, `PJE_AUTH=a3`) | **Não funciona remoto**: o token tem de estar plugado na máquina que faz o login | barbara, cinthia: ficam na instalação local (decisão de 16/11) |
| CAPTCHA (TJES 1G: AWS WAF; TJTO: imagem) | `JURISYNC_CAPTCHA_API_KEY` no ambiente do servidor; TJTO com o extra `captcha-ocr` (ddddocr) instalado | herica (TJES), denis (TJTO, pendente de usuário) |

## Inventário (portais declarados em `customers/<nome>/portals.toml`)

| Escritório | Portais | Sistemas | A1 | Outros conectores | Onda |
|---|---|---|---|---|---|
| victor | 9 | PJe | sim | ADVBOX | 1 (piloto) |
| fernanda | 40 | PJe 21, Eproc 19 | sim | — | 1 |
| herica | 51 | PJe 24, Eproc 22, e-SAJ 4, Projudi 1 | sim | — | 1 |
| anaflavia | 30 | PJe 21, Eproc 8, STJ | sim | MindChat | 1 |
| denis | 14 | PJe 7, Eproc 7 | sim | — | 1 |
| wesley | 10 | PJe 4, Eproc 6 | sim | Juridiq | 1 |
| patricia | 10 | PJe 6, Eproc 4 | sim | Juridiq | 1 |
| allan | 3 | PJe 2, Eproc 1 | sim | Juridiq | 1 |
| eliane | 14 | PJe 7, Eproc 7 | — | — | 2 |
| natalia | 12 | PJe 3, Eproc 9 | — | — | 2 |
| thaile | 12 | PJe 6, Eproc 4, e-SAJ, Projudi | — | Juridiq | 2 |
| joao | 5 | PJe 3, Eproc 2 | — | ADVBOX | 2 |
| kirlia | 5 | PJe 2, Eproc 2, e-SAJ | — | ADVBOX | 2 |
| jean | 4 | PJe 3, Eproc 1 | — | — | 2 |
| navarro | 3 | PJe 3 | — | — | 2 |
| elisandra | 3 | Eproc 3 | — | — | 2 (renomear variáveis `*_USUARIO/_SENHA`) |
| priscila | 2 | Eproc 2 | — | Juridiq | 2 |
| barbara | 17 | PJe 7, Eproc 10 | A3 | — | local |
| cinthia | 9 | PJe 5, Eproc 3, e-SAJ | A3 | ADVBOX | local |

Onda 1 = A1 (login sem navegador na maioria dos portais). Onda 2 = senha + TOTP (usa mais o Chrome do Eproc).
Onda 3 = A3: barbara e cinthia ficam na instalação local (`settings.toml` `[hosted] mode = "local"`); no
servidor existem só como registro, sem credenciais, usuários nem ferramentas (`docs/hosted.md`, "Local-only offices").
Isso vale também para os 6 portais por senha + autenticador da cinthia: o escritório inteiro fica local.

## Fases

### Fase 0 — Base (2–3 dias)
1. Revisar e subir `integrate/main` para `main` (PR), com a suíte verde.
2. Servidor de produção: uma VM Linux (Chrome headless, LibreOffice, `uv sync --extra captcha-ocr`), domínio
   próprio com HTTPS (Cloudflare Tunnel ou Caddy), `jurisync-hosted init`, `JURISYNC_VAULT_KEY` num cofre de
   segredos (systemd credential no mínimo), `JURISYNC_CAPTCHA_API_KEY`, backup diário do store + cofre.
   Tudo isso é script: `deploy/push.sh` + `deploy/install.sh` (ver `deploy/README.md`).
3. Teste de IP: um login por sistema (PJe SSO, Eproc TRF4, e-SAJ TJSP, Projudi TJGO, STJ) a partir da VM, para
   ver se algum portal desafia IP de datacenter. Se algum desafiar, saída por IP brasileiro para esse portal.
4. Ligar `JURISYNC_REQUIRE_TOKEN_RESOURCE=1` depois de testar o login OAuth no Claude e no ChatGPT.

### Fase 1 — Piloto Victor (3–4 dias)
1. `tenant add victor --customer victor`; importar o pacote do Victor portal a portal com `--check`.
2. Usuários do escritório (`user add`), conector `https://<host>/mcp` no Claude de cada um.
3. Rodar os 8 passos de "Validation per office" de `docs/hosted.md`, comparando com a instância atual
   (mesmos números de contadores/caixa/processo no mesmo dia).
4. Plugin do Victor (skills `victor-sop-intimacoes`, `victor-pje-filing`) apontando para o conector novo;
   rotinas agendadas rodando sem ninguém conectado.
5. Uma semana em paralelo; depois desligar a instância "Pje Victor Hugo". Repetir para o Fabrício se ele for
   mantido.

### Fase 2 — Onda 1 (A1) (1–1,5 semana, 2–3 escritórios por dia)
Por escritório, o checklist abaixo. Ordem sugerida por volume e por já ter validação completa hoje:
fernanda → herica → anaflavia → denis → wesley → patricia → allan.

### Fase 3 — Onda 2 (senha + TOTP) (1 semana)
Mesmo checklist. Antes: renomear as variáveis da elisandra para os nomes padrão; conferir senhas expiradas
(kirlia, thaile em 13/11) com o escritório.

### Fase 4 — Onda 3 (A3)
Decidido em 16/11: barbara e cinthia **ficam locais**, como hoje (A3 nas máquinas delas). Saídas para rever depois,
por escritório:
- **A1 para o servidor** (recomendado): o escritório emite um A1 (e-CPF) para a automação.
- **Ponte local**: um processo pequeno na máquina do escritório com o token, que só assina o desafio de login
  quando o servidor pede (exige construir e manter; o login cai quando a máquina está desligada).
- **Ficar local** (escolhido): o escritório mantém a instalação local; o servidor só guarda o registro.
- **Ponte por Tailscale** (ideia, não validada, nada construído): o notebook do escritório entra numa tailnet com o
  servidor e expõe o token A3 (assinatura do desafio de login, ou o leitor via PKCS#11 remoto) só para o servidor.
  Falta provar que os SSOs aceitam a assinatura feita assim, a latência no login, o PIN sem digitação e o que acontece
  com o notebook desligado ou fora da rede; e o termo de custódia muda (o servidor passaria a usar o token).

### Fase 5 — Página do escritório e modelo de contas (rollout-plan fase B)
Construir a tela de cadastro (login, senha, QR/A1, teste e grava), para que trocas de senha e certificado novo
não passem por nós. Até lá, mudança de credencial = novo pacote + `import-bundle --check`.

### Depois — Cache e sincronização (rollout-plan fases C–D)
Começa quando houver uns 5 escritórios no servidor: reduz logins e requisições repetidas.

## Checklist por escritório

1. **Antes**: `uv run python -m jurisync.customers <nome>` com todos os portais `ready`; relatório de validação do escritório
   atualizado; acordo de custódia do A1 e das senhas assinado (LGPD).
2. **Cofre**: `jurisync-hosted provision export` na nossa máquina (plano + pacote por escritório) → `provision apply`
   no servidor → `deploy/check-office.sh <nome>` (um login por portal). Forma manual: `jurisync-setup export` na nossa máquina → `secrets import-bundle <nome> <arquivo> --portal <p> --check`
   para cada portal (um login por portal, nada é gravado se falhar) → `portal list <nome>`.
3. **Acesso**: `user add` para cada advogado; cada um adiciona `https://<host>/mcp` no Claude e entra.
4. **Prova**: uma leitura por sistema com a conta do escritório (contadores PJe, processo Eproc, DJEN pela OAB,
   ADVBOX/Juridiq); `jurisync-hosted calls <nome>` mostra as chamadas.
5. **Skills e rotinas**: plugin do escritório instalado; rotina agendada testada sem ninguém conectado.
6. **Desligar o local**: remover os servidores locais (`.mcp.json`) da máquina do escritório e apagar o `.env`
   e o A1 de lá, depois de uma semana sem incidentes.

## Regras que continuam valendo no servidor

- Um login por (escritório, portal) no pool; senha recusada não é reenviada no período de espera (15 min).
- Nunca ciência automática: abrir ato de intimação continua só sob pedido explícito.
- Escrita: PJe desligada; ADVBOX/Juridiq só com `*_ALLOW_WRITES` do escritório.
- Registro de chamadas sem argumentos nem resultados; 90 dias.

## Decisões que precisamos de você

1. Onde roda a instância atual do Victor (e do Fabrício), e se o servidor novo fica no mesmo provedor.
2. Domínio e provedor da VM de produção (e se usamos Cloudflare Tunnel como no `tema324`).
3. ~~Saída para os escritórios com A3 (barbara, cinthia)~~: ficam locais (16/11).
4. Texto do termo de custódia do A1/senhas antes da onda 1.
