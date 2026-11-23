# Roadmap do Jurisync (23/11/2026)

Documento único de planejamento. Substitui `implementation-plan.md`, `rollout-plan.md`,
`remote-mcp-migration-plan.md`, `architecture-review-2026-11-10.md`, `review-2026-11.md`, `sync-plan.md` e
`backlog.md` (em `docs/archive/`, só como histórico). Referência que continua valendo: `architecture.md`,
`hosted.md`, `performance.md`, `service-layer-design.md` (desenho para depois), `connectors/`, `validation/`,
`progress/`.

## Estado atual (conferido no código)

**Um código só, dois modos de uso.**

- **Instalação local**: servidores MCP por stdio (`.mcp.json`) ou `jurisync-http` (`install/serve.py`), credenciais
  no `.env` do escritório. `jurisync-setup export` gera um pacote cifrado com senha; `jurisync-setup import` instala
  na máquina do escritório e testa os logins (`install/setup.py`, `install/credentials_bundle.py`).
  Escritórios com token A3 ficam locais: `customers/<nome>/settings.toml` com `[hosted] mode = "local"` (hoje barbara
  e cinthia). No servidor eles existem só como registro, sem credenciais, usuários nem ferramentas.
- **Servidor hospedado** (`jurisync-hosted`, `src/jurisync/hosted/`): OAuth por escritório, cofre AES-256-GCM por
  escritório, uma URL (`/mcp`) com as ferramentas dos conectores do escritório; cada ferramenta só oferece os portais
  em que o escritório tem login. `provision export/apply` monta escritórios a partir de `customers/`. Detalhes em
  `hosted.md`; implantação em `deploy/README.md`.
- **Registro único** (`src/jurisync/registry.py`): a lista de conectores que `install/serve`, `install/setup` e
  `hosted/` leem. Ferramentas de rascunho do PJe (`pje_draft_*`) só na máquina do escritório; escrita de ADVBOX e
  Juridiq só com `ADVBOX_ALLOW_WRITES`/`JURIDIQ_ALLOW_WRITES` do próprio escritório.

**Conectores** (`registry.py`): PJe (inclusive PJe-JT/KZ), Eproc, e-SAJ, Projudi, STJ, Tucujuris, JPe, DJEN, ADVBOX,
Juridiq. Fora do registro: `jurisync-filing` (preparação de protocolo, local) e o cliente MindChat
(`connectors/mindchat/api.py`, usado pelo fluxo de petições).

**Bloco de ciência** (`core/ciencia.py`): toda ferramenta que abre ato ou documento devolve `ciencia.estado` =
`nenhuma`, `dada`, `pendente` ou `desconhecida`, com `aviso` em português para repassar ao usuário. Ler pode dar
ciência; o usuário sempre fica sabendo o que a leitura fez. Por sistema, hoje:

- Eproc: abre os documentos pedidos e informa (`dada`/`pendente`/`desconhecida`).
- PJe, Projudi e JPe: um ato pendente volta como `pendente` (nada aberto) até o usuário concordar
  (`acknowledgeCiencia=true`; no PJe também `[pje] ciencia_on_read = "allow"` no `settings.toml`).
- e-SAJ, Tucujuris e STJ: nunca abrem o que daria ciência.

**Sessões** (`core/session_pool.py`): uma sessão por (escritório, portal), reaproveitada entre chamadas.
`sessions.run` refaz o login uma vez quando a sessão expira (PJe, Eproc, e-SAJ, Projudi, STJ, Tucujuris, JPe). No
máximo `JURISYNC_MAX_LOGINS` logins ao mesmo tempo; senha recusada não é reenviada por 15 min
(`JURISYNC_LOGIN_COOLDOWN_SECONDS`). Só portais configurados para o escritório abrem login (`SessionPool.only`).
No servidor, sessões, clientes ADVBOX, tokens Juridiq e o bloqueio de 15 min guardam a assinatura das credenciais do
escritório (`credentials.signature()`): uma troca feita pelo `jurisync-admin` vale na chamada seguinte, sem reiniciar.
Todo login falho traz um motivo fixo (`core/session_pool.REASONS`) com a explicação em português.

## Feito nesta rodada de consertos (branch `chore/hardening`)

- `0a5cdab` Fernanda: 41 portais validados de ponta a ponta; certificado e perfil TOTP fixados.
- `4cd2d6d`, `ecb2d16`, `5ef8ed8`, `f3a5ae9` Eproc listas/documentos/TRF6 com A1 e PJe Push (lista vazia).
- `e373f00` Núcleo comum: bloco de ciência, portais oferecidos, guarda de login, credenciais renováveis
  (`credentials.save` grava no `.env` local ou no cofre).
- `7fb9691` Registro único de conectores para a instalação local e o servidor; Tucujuris e JPe no servidor.
- `465dada` PJe: sessão expirada refaz o login; busca do processo reaproveitada 180 s (`get_process` →
  `read_documents` → `read_expediente_ato` cai de 6–8 para 1–2 requisições).
- `a8aa101` Eproc, e-SAJ e Projudi: `sessions.run`, bloco de ciência, portais configurados, títulos.
- `63536ef` STJ, JPe, Tucujuris, Juridiq e MindChat: portais configurados, bloco de ciência, valores por escritório.
- `269488f` ADVBOX: datas AAAA-MM-DD e verificação do aparelho com o código pedido ao usuário.
- `eb4d834` Login: motivo da falha padronizado em todos os sistemas (senha, senha expirada, TOTP, A1, sem cadastro,
  bloqueio, CAPTCHA, portal fora, login faltando); um só carregador de A1 (`core/certificate.load_configured`) e um só
  código TOTP (`core/totp`) para PJe, Eproc, Projudi, e-SAJ, STJ, JPe e Tucujuris.
- `0e5f779` Ciência com autorização prévia; tabela do Push do PJe; motivo de falha nos downloads do PJe.
- `3876c04`, `d8924ca` Consertos da revisão: servidor remoto (portal check do PJe-JT, `provision apply` substitui o
  cofre, `Vault.update` com trava), portais do perfil, deploy; `.env` sem quebra de linha, Push e downloads, código
  TOTP fresco, verificações ADVBOX expiradas.
- Rodada de causa raiz:
  - `3039bec` PJe: downloads lentos do TJBA (prazos por etapa), link `frameBinario` do TJPA, erros de fluxo do
    Keycloak (sessão inválida/ação expirada) com uma nova tentativa, busca do PJe-JT reaproveitada 180 s, OCR de PDF
    escaneado (`core/ocr.py`, extra `pdf-ocr`).
  - `bac29ab` e-SAJ: pasta digital pelo botão "Visualizar autos", "múltiplas consultas simultâneas" repetida
    (rate limit) e espera de 150 s do painel.
  - `b99b207` Projudi TJGO: filtros da busca limpos antes de procurar por número; Eproc pelo Chrome: motivo e
    diagnóstico no login.
  - `52a7970` Projudi TJBA: resolvedor do CAPTCHA Tencent (`core/tencent_captcha.py`) e adaptador cnj-servlet,
    **ainda não ligado** (`wired=False`) até um login real com senha válida (Thaile: senha expirada; Max: recusada).
- Listagens que faltavam (branch `feat/listing-gaps`, validadas ao vivo em 23/11):
  - PJe: `pje_list_processes` lista o Acervo do painel (jurisdição → caixa → páginas; no PJe-JT, Acervo Geral +
    Arquivados pela API). Juliana: tjmg-pje-1g 197/197 em 53 jurisdições, tjce-1g 19/19, `complete`. PJe 1.x fora.
  - Projudi TJAM: `projudi_list_intimations` (listas de intimações/citações da mesa, "Novas" com `cienciaPendente`)
    e `projudi_list_processes` (1º/2º grau por situação). Thaile: 1 processo, 2 intimações com prazo perdido,
    0 aguardando ciência.
  - e-SAJ: `esaj_list_processes` lista os processos de 1º grau pela consulta por OAB de cada OAB da sessão
    (paginada, `complete` por OAB). Juliana: TJAL 5, TJMS 1 (via token). TJAM sem suporte (portal antigo sem
    auth/session); 2º grau fora.

## Próximas etapas (em ordem)

1. **Terminar a limpeza**: um só fluxo Keycloak/x509/OTP para PJe, Eproc e Projudi (hoje `pje/auth/sso.py`,
   `eproc/auth.py`/`http_login.py` e `projudi/keycloak.py`); ligar o Projudi TJBA quando houver uma senha válida.
2. **Testar o servidor com escritórios reais**, um de cada vez, com o checklist "Validation per office" de
   `hosted.md`:
   - piloto: victor (comparar com a instância atual "Pje Victor Hugo" por uma semana antes de desligá-la);
   - onda 1, A1 (login sem navegador na maioria): fernanda → herica → anaflavia → denis → wesley → patricia → allan;
   - onda 2, senha + TOTP (mais Chrome no Eproc): eliane, natalia, thaile, joao, kirlia, jean, navarro, elisandra,
     priscila;
   - barbara e cinthia continuam locais (A3).
   Antes da onda 1: teste de IP de datacenter por sistema, `JURISYNC_REQUIRE_TOKEN_RESOURCE=1` depois de testar o
   login no Claude e no ChatGPT, termo de custódia do A1 e das senhas (LGPD).
3. **Depois: camada de serviço** (desenho em `service-layer-design.md`): medição de uso por chamada (requisições,
   logins, origem), cache das leituras ao vivo com single-flight, read model por escritório + sincronização (DJEN,
   contadores, caixa, processos ativos), ferramentas de fluxo (`jurisync_whats_new`, `jurisync_agenda`,
   `jurisync_process_overview`), API HTTP para a página do escritório e relatórios. Começa com uns 5 escritórios no
   servidor.
4. **Padronizar nomes de ferramentas entre sistemas.** As de caixa de intimações hoje: `pje_list_inbox`,
   `eproc_list_notices`, `projudi_list_intimations`, `tucujuris_list_intimacoes`, `jpe_list_communications`,
   `esaj_list_messages`, `stj_list_inbox`. Renomear junto com as skills e plugins dos escritórios, conector por
   conector; nunca renomear sem atualizar as skills na mesma entrega.

## Pendências conhecidas

Do antigo `backlog.md` e dos planos arquivados, ainda abertas e não cobertas acima:

- **Página do escritório** (cadastro self-service de senha, TOTP/QR, A1): lógica pronta em `hosted/access.py`, tela
  não existe. Até lá, troca de credencial = novo pacote + `secrets import-bundle --check`.
- **Servidor**: pedidos de login OAuth em memória (um processo só); chave-mestra única (KMS/chaves por escritório e
  rotação); papéis de usuário (admin/membro), convites, 2FA no login do Jurisync; aviso quando o refresh de uma rotina
  agendada falha; limites por escritório; ambiente de homologação.
- **Arquivos para o cliente remoto**: documentos salvos (`save=true`, downloads ADVBOX) ficam no servidor; falta
  devolver como `resource_link` ou link assinado.
- **PJe Expedientes e Push**: detectar expedientes novos entre execuções (guardar `expedienteId` vistos); filtros de
  órgão julgador e prioridade; ler os prefixos dos outros buckets num perfil com itens. Push continua só leitura.
- **Eproc**: fixtures revisadas de PJe e Eproc dos mesmos tipos de processo; paginação além da página 0, documentos
  restritos e PDFs escaneados conferidos ao vivo.
- **Sem conector ainda**: Meu INSS/GERID (navegador + CAPTCHA), Jurichat.
- **Decisões em aberto**: onde roda a instância atual do Victor (e do Fabrício, hoje fora do ar) e provedor/domínio
  da VM de produção.
- **Instalação local**: `jurisync-http` local sem autenticação; `.env` raiz compartilhado ainda tem valores comuns
  (flags de escrita) que deveriam ir para o `.env` de cada escritório.

## Decisões tomadas

- Configuração na instalação, não em tempo de execução: escolhas como o perfil TOTP são mapeadas uma vez no setup e
  gravadas no `.env`; nada de adivinhar no login.
- Escritórios com A3 ficam na instalação local (`[hosted] mode = "local"`); o servidor guarda só o registro.
- Ciência: o usuário é sempre informado pelo bloco `ciencia`; ler pode dar ciência. Nunca ciência automática
  silenciosa.
- Uma branch base, `main` (mais `feat/hosted-rollout` no remoto); nada de branch por cliente ou por portal.
- Sem microserviços, Celery/Redis ou Postgres por enquanto: um processo, SQLite por escritório quando houver store.
- Sem sincronizar ADVBOX/Juridiq nem históricos inteiros; sem guardar texto de documento antes da ciência.
