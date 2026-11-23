# Revisão da suíte de testes (23/11/2026)

Limpeza aprovada da suíte automática (`tests/`). Este arquivo registra o que ficou para depois e por quê.

## Feito

- Os 2 testes quebrados foram corrigidos:
  - O teste do AWS WAF do login PJe (TJES) agora é coletado e usa a assinatura atual de `_login`.
  - `djen::test_fetch_link_document_guards` não acessa mais a rede.
- `stj::test_no_tool_ever_sends_a_write` agora falha se uma escrita for tentada.
- `tests/hosted` ficou rápido:
  - O SQLite dos testes roda em tmpfs (`/dev/shm`).
  - O scrypt dos testes usa um custo baixo (`tests/conftest.py`). A produção não muda.
- Removidos os 13 redundantes de alta confiança (H1–H13 da revisão). As asserções únicas de cada um foram para o teste que ficou.
- Reescritos ou removidos os testes que só conferiam o próprio mock, uma constante ou o texto de uma descrição.
- Novos testes:
  - Travas de escrita: rascunhos PJe, ADVBOX e Juridiq (MCP e hospedado).
  - Isolamento por escritório: Eproc, STJ, JPe, Tucujuris e Juridiq.
  - Ciência pelo servidor MCP do PJe (`pje_read_documents` e `pje_read_expediente_ato`).
  - Tokens do servidor hospedado: `/revoke`, refresh já usado e escritório removido.

## Achado no `/revoke` (não corrigido; fora de `tests/`)

O `RevocationRequest` do SDK MCP declara `client_secret: str | None` sem valor padrão, então o campo é obrigatório. Um cliente público (`token_endpoint_auth_method = none`, caso do Claude) que chama `/revoke` sem `client_secret` recebe **400** "client_secret: Field required", e o token **não é revogado**.

O teste novo manda `client_secret=""` para exercitar a revogação do Jurisync. A correção fica para depois. Duas opções:

- aceitar o formulário sem o campo antes do handler do SDK;
- atualizar o SDK quando ele corrigir.

## Deixado para depois (média confiança)

São fusões e remoções que economizam pouco (cerca de 2,5 s no total, metade disso já resolvida pela aceleração do hospedado). Cada uma pede cuidado para não perder uma asserção única.

| # | Teste | Por que esperar |
|---|---|---|
| M1 | `pje/test_pje_filing_phases.py::DocumentBatchTests`: lote de um arquivo, e arquivo já no rascunho | Wrapper de uma linha sobre `add_supporting_documents`, coberto por `test_pje_filing_resilience.py::ReconcileTests`. Conferir antes se o "verified after upload" aparece lá. |
| M2 | `pje/test_pje_kz.py::test_login_rejects_the_pre_registration_page` | Mesmo ramo `_require_registered` do teste de cert login. As URLs são sintéticas nos dois, mas este é o único do caminho KZ. |
| M3 | `pje/test_pje.py::test_authenticator_migration_accounts` | Só remover depois de confirmar que `core/test_totp.py` cobre `digits` e `issuer`. |
| M4 | `eproc/test_eproc_client.py`: os dois testes de expiração com HTTP 200 | Mesmo caminho, só muda a aspa. Fundir com subTest. |
| M5 | `eproc/test_eproc_state_courts.py::test_state_link_with_another_action_is_ignored` | Coberto de ponta a ponta pelo teste de fila de painel estadual. Unitário barato; manter até a próxima mudança no Eproc. |
| M6 | e-SAJ e Projudi tenants: os dois pares de `load_certificate` | Ambos embrulham `core.certificate.load_configured`. Mover um par para `core` e apagar o outro. |
| M7 | `eproc/test_eproc_certificate.py::test_keycloak_that_asks_the_code_right_after_the_certificate` | Subconjunto do teste TJSC. Manter enquanto a URL de entrada do TJPR precisar de cobertura própria. |
| M8 | `projudi/test_keycloak.py::TjrrTest::test_wrong_password_and_captcha_stop_after_one_post` | Mesmo código do `LoginTest`, com outras URLs. Depende da decisão sobre o adaptador keycloak (abaixo). |
| M9 | `tucujuris/test_tucujuris.py::test_rejected_login_is_not_retried` | Coberto pela regra do pool mais `LoginTest`. É o único teste da recusa pelo servidor do Tucujuris. |
| M10 | Caso `[204-]` de `esaj/test_esaj.py::test_document_permission_cleared_only_by_204_or_an_empty_200` | Repete `test_document_bounded_pdf_and_close`. Ganho mínimo. |
| M11 | `djen/test_djen_render_cli.py`: as duas checagens de página parcial | Mesma entrada; uma confere o código de saída, a outra a dica. Fundir. |
| M12 | `djen/test_djen.py::test_cli_all_and_variants_write_json_to_stdout` | Os filtros por página e variante têm teste melhor. Confirmar que nenhum outro teste cobre a saída JSON em stdout. |
| M13 | Casos `not-a-url` e `/content/posts` de `advbox/test_advbox.py::test_download_url_guard` | Mover para `test_advbox_write_safety.py` e apagar o original. |
| M14 | `djen/test_djen_render_cli.py::test_default_stays_json_items` | Coberto por `test_search_normalizes_items`. |
| M15 | `workflows/test_filing_server.py`: pasta não baixada, e prepare que falha | Mesma chamada. Fundir. |
| M16, M17 | Testes de sessão por tenant em `hosted/test_hosted.py` e `hosted/test_onboarding.py` | Cobertos pelo teste do pool em `core`. Consolidar os testes de `SessionPool`, hoje espalhados por cinco arquivos. |
| M18 | `test_onboarding::test_throttle_blocks_after_repeated_failures` | Antes de fundir com `AvailabilityTest`, mover as duas asserções únicas. |
| M19 | `hosted/test_office_endpoint.py::StrictCombinedAudienceTest` | `StrictAudienceTest` cobre mais. Remover. |
| M20, M21 | `hosted/test_hosted.py::HostedAppTest`: chamadas por escritório, e senha errada | Mover o texto "incorretos" e os dois testes únicos para `SignInCase`, e apagar os helpers duplicados de `pkce` e `sign_in`. |
| M22 | `core/test_credentials_bundle.py::test_export_then_import_writes_the_same_variables` | Mover antes a checagem de senha errada com saída 2. |
| M23 | `core/test_mcp_runtime.py::SessionPoolTest::test_reuses_one_login_per_key` | Faz parte da consolidação de M16. |

### Ainda abertos da seção 3

- **Testes que conferem `__code__.co_names`** (`projudi/test_legacy_local.py` e `projudi/test_keycloak.py`): frágeis, mas guardam a regra "sem login por certificado". Trocar por um teste de comportamento quando o adaptador for mexido.
- **Testes que repetem literais do registro** (`core/test_portals.py`, `pje/test_pje_kz.py::test_only_the_judicial_portals_use_the_rest_flavor`, `hosted/test_connectors_hosted.py::test_only_tenant_aware_connectors_mount`, `core/test_customers.py::test_victor_setup_is_valid`): quebram a cada portal novo. Parametrizar sobre `customers/*` e as regras, em vez de listar chaves.
- **Constantes mortas em `projudi/test_legacy_local.py`**: `FRAMESET`, `MAIN`, `SEARCH` e `RESULT`.
- **`core/test_transcription.py`**: o teste com áudio real deveria levar a marca `live`.
- **`tests/live`**:
  - `require()` do Eproc ainda usa os nomes legados.
  - O Victor não lê `customers/victor/.env`.
  - Os tempos testados são frágeis.
  - Não há smoke para e-SAJ, Projudi, STJ, Tucujuris e JPe.

### Decisão de equipe

`src/jurisync/connectors/projudi/keycloak.py` (TJPR/TJRR) não é importado por nenhum módulo de `src`. Seus 32 testes cobrem código não ligado. Se o adaptador for ligado, os testes ficam. Se não for, adaptador e testes podem sair juntos.

### Lacunas que continuam

- **PJe:** abrir um ato pendente nos PJe antigos sem REST.
- **JPe e Tucujuris:** testes `call_tool` de ciência.
- **STJ:** a ciência vem sempre como "nenhuma", mesmo com vistas abertas.
- **Eproc:** o A1 por escritório no login por certificado (o carregamento é o mesmo `load_configured` já testado no e-SAJ e no Projudi).
- **Eproc:** redirect para outro host em `test_fetch_process_follows_only_same_host_search_redirect`.
- **Hospedado:**
  - `check_password` com hash malformado;
  - cofre com a chave errada.
