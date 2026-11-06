# Projudi — mapa dos tribunais e adapters

Levantamento público e read-only em **2026-11-05**, por subagentes, sem
credenciais e sem login nos tribunais além do TJGO. O objetivo é decidir o
desenho do conector `projudi` e o que cada tribunal exige.

**Conclusão curta:** Projudi não é um sistema homogêneo. O TJGO é a geração
nova com A1/mTLS; TJPR e TJRR usam Keycloak com TOTP; TJAM usa login local com
certificado interno; TJPI é uma distribuição antiga (1.9.8) com CAPTCHA. O
conector precisa de **core compartilhado + adapter por flavor**, e as rotas do
TJGO não são reutilizáveis.

## Matriz

| Tribunal | Versão observada | Login | 2FA | Certificado | Consulta pública | Documentos | Adapter | Status |
|---|---|---|---|---|---|---|---|---|
| **TJGO** | servlet novo | `/LogOn` + `/certificado` A1/mTLS | não no cert | A1 ICP-Brasil validado | pública | índice JSON + download PDF/HTML validados | `tjgo-servlet` | **validado** |
| **TJPR** | v25.0.28 | `sso.projudi.tjpr.jus.br`, realm `projudi`, client `projudi`; CPF/CNPJ + senha | TOTP (Google Authenticator) | botão A3 → `projudicrt.tjpr.jus.br` mTLS; A1 é hipótese | `consulta.tjpr.jus.br` com reCAPTCHA | desconhecido sem login | `legacy-keycloak` | não validado |
| **TJRR** | v5.12.33.1 | `keycloak.tjrr.jus.br`, realm `projudi`, client `projudi-client`; CPF + senha | TOTP obrigatório (documentado) | não usa A1/mTLS no portal | SPA `consultaprojudi.tjrr.jus.br` + captcha | desconhecido sem login | `legacy-keycloak` | não validado |
| **TJAM** | v2026.1.11 | form local `/projudi/usuario/logon.do`; `login`/`senha` | não observado | **sem A1/mTLS**; certificado interno do Projudi | F5/reCAPTCHA | `arquivo.do?id=` observado, protegido | `legacy-local` | não validado |
| **TJPI** | 1.9.8 (2011) | form local `POST /projudi/publico/Logon`; `login`/`senha` | não observado | **sem A1/mTLS**; assinador externo só para assinatura | CAPTCHA obrigatório | `DownloadArquivo?arquivo=` observado, 0 bytes sem sessão | `tjpi-publico` | legado; não validado |

Detalhes por tribunal:

- [TJGO](projudi-tjgo.md) — validado: login A1/mTLS, processo, índice de
  documentos, anexos por movimentação, download e extração.
- [TJPR](projudi-tjpr.md) — Keycloak externo, TOTP, botão de certificado A3,
  consulta pública com reCAPTCHA, sem JSON público.
- [TJRR](projudi-tjrr.md) — Keycloak, TOTP, `autoSSO` local, consulta SPA com
  captcha, bloqueio geográfico documentado.
- [TJAM](projudi-tjam.md) — frameset legado, login local, certificado interno,
  consulta pública atrás de F5/captcha.
- [TJPI](projudi-tjpi.md) — Projudi 1.9.8/CNJ, Tomcat 6, CAPTCHA, sistema
  desativado para novas distribuições (provimento citado pelo tribunal).

## Credenciais necessárias para validar de verdade

| Tribunal | O que falta |
|---|---|
| TJGO | A1 `.p12` + PIN — **já validado** com a cliente |
| TJPR | CPF/CNPJ + senha + TOTP; ou teste do A1/A3 no `projudicrt` (mTLS) |
| TJRR | CPF + senha + TOTP; cadastro manual via OAB-RR/TJRR; IP brasileiro |
| TJAM | usuário/senha local; certificado interno para assinatura, não para login |
| TJPI | usuário/senha local; sistema legado, CAPTCHA na consulta pública |

Nenhum desses quatro tem credencial no repo. O que foi validado é o
**mecanismo** de credenciamento (formulário, SSO, TOTP, certificado), não uma
sessão autenticada. Não tentar login com o A1 da Ana nos outros tribunais sem
confirmação de que ele está cadastrado/credenciado lá.

## Adapters

```text
connectors/projudi/
  base.py        # sessão, cookies, A1/PEM, limites, texto, erros
  tjgo.py        # flavor "tjgo-servlet" (validado)
  keycloak.py    # TJPR/TJRR: OAuth code + TOTP, configurado por portal
  tjam.py        # flavor "legacy-local" (form login, certificado interno)
  tjpi.py        # flavor "tjpi-publico" (Projudi 1.9.8, CAPTCHA)
  server.py      # tools MCP; escolhe o adapter pelo portal
```

Regras:

- todas as tools recebem `portal` (default `tjgo-1g`);
- `wired=False` para TJPR/TJAM/TJRR/TJPI até smoke live;
- não reutilizar rotas/parsers do TJGO nos outros;
- credenciais por prefixo (`TJPR_*`, `TJRR_*`, `TJAM_*`, `TJPI_*`), A1 global só
  no TJGO;
- CAPTCHA, TOTP e cadastro manual são bloqueios de automação; documentar em vez
  de contornar.

## Ordem sugerida de implementação

1. **TJGO** — concluir o conector validado (em andamento com o Claudio).
2. **TJPR** — maior chance de reuso do padrão Keycloak; validar A3/A1 no
   `projudicrt` e mapear documentos com uma credencial real.
3. **TJAM** — login local simples, mas documentos e consulta atrás de
   F5/captcha; mapear rotas autenticadas.
4. **TJRR** — Keycloak + TOTP + bloqueio geográfico; depende de credencial e
   liberação de IP.
5. **TJPI** — avaliar se vale implementar: sistema legado/desativado, CAPTCHA e
   documentação escassa.

## Riscos e questões abertas

- TJPR: o A1 é aceito no mTLS do `projudicrt` ou o app exige política A3?
- TJPR/TJRR: como obter o segredo TOTP para automação? Sem isso, login manual.
- TJRR: bloqueio geográfico e limite de sessões.
- TJAM: rotas autenticadas de busca/movimentações/documentos não mapeadas.
- TJPI: sistema em fim de vida; confirmar se ainda aceita novos usuários.
- Nenhum tribunal além do TJGO expôs A1/mTLS na sondagem pública.
- Consulta pública com captcha não serve de fallback automatizado.
