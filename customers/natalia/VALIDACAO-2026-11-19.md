# Natália (Rasi Advocacia): validação do conector PJe em 19/11/2026

Testes ao vivo com `JURISYNC_CUSTOMER=natalia`, só leitura e em modo headless, usando as funções do próprio conector (`login_portal`, `read_counters`, `read_inbox`, `read_push`, `fetch_process`, `read_documents` com `acknowledge_ciencia=False`). Não há certificado A1: todo portal entrou com CPF, senha e TOTP pelo SSO do PDPJ (`sso.cloud.pje.jus.br`, realm `pje`). O login é o da Ana Flávia Pacheco Ramos, autorizado pelo escritório. O `.env` agora usa `<PREFIX>_OTP_PROFILE_INDEX=0` (perfil "PDPJ/PJE") e `OTP_PROFILE_MATCH` vazio em todos os prefixos PJe. Este arquivo não traz senha, CPF, código, QR, cookie ou nome de parte.

## Resultado

| Portal | Login | Contadores/caixa | Processos | Processo (timeline) | Documento | Observação |
|---|---|---|---|---|---|---|
| `trf1-1g` | ✅ | ✅ todos os buckets em 0; caixa vazia | ✅ Push: 8 | ✅ 1002241-74.2020.4.01.3811: 29 movimentos, 42 documentos, 5 expedientes (0 abertos) | ✅ Substabelecimento (1138465260), 1.121 caracteres | Senha e perfil de OTP confirmados hoje |
| `trf3-1g` | ✅ | ✅ 3 pendentes, 1 com prazo findo, 9 respondidos; caixa: 3 linhas em 3 caixas | ✅ Push: 47 | ✅ 5039217-49.2023.4.03.6301: 15 movimentos, 18 documentos, 1 expediente (0 aberto) | ✅ Certidão de Trânsito em Julgado (375787504), 518 caracteres | Escolhi um processo fora da caixa de pendentes |
| `trf3-2g` | ✅ | ✅ tudo em 0; caixa vazia | ✅ Push: 8 | ✅ 5001511-66.2024.4.03.6343: 20 movimentos, 114 documentos, 7 expedientes (0 abertos) | ✅ Certidão de Trânsito em Julgado (368480817), 749 caracteres | |
| `trf5-1g` | ✅ | ✅ 1 pendente, 1 sem prazo; caixa: 1 linha | ✅ Push: 3 | ✅ 0011194-71.2023.4.05.8000: 14 movimentos, 16 documentos, 4 expedientes (0 abertos) | ⚠️ Procuração/Habilitação (42449182): PDF baixado (HTTP 200), mas `ocr_needed` e 0 caracteres | Sem A1, entrou por senha + TOTP. O PDF é digitalizado, por isso não veio texto |
| `tjrj-1g` (PJe TJRJ) | ✅ | ✅ tudo em 0; caixa vazia | Push: 0 | não testado | não testado | **Login ok, sem processos** visíveis no Push e na caixa. O conector não lista o acervo, então um processo fora do Push não aparece aqui |
| `tjpa-1g` | ✅ | ✅ tudo em 0; caixa vazia | ✅ Push: 2 | ✅ 0802512-14.2023.8.14.0013: 40 movimentos, 59 documentos, 12 expedientes (0 abertos) | ⚠️ Documento de Comprovação (98734222): PDF baixado, `ocr_needed`, 0 caracteres | O TJPA aceitou o login; a "senha expirada" vista antes não apareceu. O PDF é uma imagem (prints) |
| `tjba-1g` | ✅ | ✅ 1 pendente, 1 "apenas pendente de ciência", 1 "pendente de ciência pelo judiciário"; caixa: 1 linha | ✅ Push: 3 | ✅ 8003997-24.2025.8.05.0110: 39 movimentos, 70 documentos, 15 expedientes (0 abertos) | ✅ Certidão (584087369), HTML, 390 caracteres | O processo com intimação pendente (8007444-83.2026.8.05.0110) não foi aberto |
| `tjmg-pje-1g` | ✅ | ⚠️ contadores ok (15 pendentes, 13 com ciência do Judiciário, 11 com prazo findo, 2 sem prazo, 8 respondidos), mas a **caixa veio vazia** (0 caixas, 0 linhas, `complete=true`) | ✅ Push: 20 | ✅ 5004935-56.2020.8.13.0261: 30 movimentos, 109 documentos, 16 expedientes (0 abertos) | ✅ Documento de Comprovação (1025694800), PDF, 3.392 caracteres | Bug na leitura da caixa (ver abaixo) |
| `tst` | ❌ | — | — | — | — | `LoginRejected: The portal rejected the username/password` (o formulário `kc-otp-login-form` não voltou de `sso.cloud.pje.jus.br/auth/realms/pje/login-actions/authenticate`). **Parei todos os logins seguintes** |
| `trt1-1g` | não tentado | — | — | — | — | Bloqueado pela regra de parada depois da recusa no TST |
| `trt1-2g` | não tentado | — | — | — | — | idem |
| `trt2-1g` | não tentado | — | — | — | — | idem |
| `trt3-1g` | não tentado | — | — | — | — | idem |
| `trt3-2g` | não tentado | — | — | — | — | idem |
| `trt5-1g` | não tentado | — | — | — | — | idem |
| `trt12-1g` | não tentado | — | — | — | — | idem |
| `trt15-1g` | não tentado | — | — | — | — | idem |
| `trt18-1g` | não tentado | — | — | — | — | idem |

**Tentativas de login:** 1 em cada portal de `trf1-1g` a `tst` (9 no total) e 0 nos TRTs. Nenhum portal teve 2ª tentativa.

Em todos os portais que entraram, o SSO foi o mesmo: `loginForm` e depois `kc-otp-login-form`, ambos em `sso.cloud.pje.jus.br`.

## Por que a recusa do TST parou tudo

- O TST usa o **mesmo provedor de identidade** do TRF1: host `sso.cloud.pje.jus.br`, realm `pje`. Muda só o client (`pje-tst-3g` no TST, `pje-trf1-1g` no TRF1) e o tema da página (`kc-form-login` em vez de `loginForm`). Os campos são os mesmos (`username`, `password`, `pjeoffice-code`, `phrase`, mais um `credentialId` oculto). Conferi isso só com GET, sem enviar credencial.
- O usuário e a senha guardados para o TST são **idênticos** aos do TRF1 e aos de todos os outros prefixos. Comparei só a igualdade, sem imprimir os valores.
- Pela regra, a recusa veio do mesmo IdP, então não houve mais nenhum login (nenhum TRT). É a 2ª recusa no TST (a outra foi em 17/11), e os portais da Justiça comum aceitaram a mesma senha minutos antes.
- Hipótese: a recusa pode não ser de senha. `credential_failure` (`auth/sso.py`) classifica como "rejected the username/password" qualquer página que contenha "invalid", "inválido" ou "usuário ou senha". Uma página de erro do Keycloak com outro motivo (client, sessão, cookie) cairia na mesma mensagem. O conector não guarda o texto do alerta, então não dá para separar os dois casos sem mais uma tentativa, que não fiz.

## Bugs do conector encontrados (nada foi corrigido)

1. **TJMG PJe 1G, leitura da caixa (`read_inbox` / `read_counters(bucket=...)`):** o bucket 0 mostra 15 itens, mas a árvore de regiões dele volta vazia (`notifications: []`). Com isso `read_inbox` devolve 0 caixas e 0 linhas e ainda diz `complete=true`. Um agente concluiria que não há pendências. No mínimo, `complete` deveria ser `false` quando a soma lida é diferente da contagem do bucket.
2. **Login no SSO, diagnóstico (`credential_failure`):** os marcadores amplos ("invalid", "usuário ou senha") transformam qualquer erro do Keycloak em "rejected the username/password", que gera `LoginRejected`, e a mensagem não traz o texto do alerta da página. No TST isso impede saber se o problema é a senha ou o client `pje-tst-3g`.
3. Não é bug: PDFs digitalizados voltam `ocr_needed` com 0 caracteres (TRF5 e TJPA). O download funcionou; só falta OCR.

Não houve erro de parse nem crash nas etapas de contadores, caixa, Push, processo e documento dos 8 portais que entraram.

## Pendências com o cliente

- **TST / PJe-JT:** pedir à Ana Flávia que entre uma vez pelo navegador em `pje.tst.jus.br` (botão PDPJ) com o mesmo CPF e senha e diga qual mensagem aparece. Isso mostra se a conta tem acesso pelo client do TST ou se há bloqueio ou pré-cadastro no PJe-JT. Até lá, ninguém deve tentar os TRTs: são o mesmo SSO e a mesma conta, e cada falha aproxima de um bloqueio.
- **TJRJ PJe:** login ok, mas Push e caixa estão vazios. Confirmar se o escritório tem processos no PJe do TJRJ e, se tiver, cadastrá-los no Push para o conector enxergar.
- **TJMG PJe:** 15 expedientes pendentes que o conector ainda não lista (bug 1). Por enquanto, conferir manualmente.

## Limites respeitados

- Só leitura: nenhum `acknowledgeCiencia`, nenhum ato de expediente aberto e nenhum documento de processo com intimação pendente. Todos os documentos lidos são de processos com 0 expedientes abertos e com a lista de expedientes completa. Nenhuma troca de senha, liberação de dispositivo, alteração de perfil ou rascunho.
- Logins em sequência, cada um numa janela nova de TOTP de 30 s. Modo headless.
- O cache de sessão do PJe ficou numa pasta temporária em `/tmp/claude-1000/`, apagada no fim junto com os scripts. Nenhuma alteração em `.env`, `portals.toml` ou código.

# Eproc em 19/11/2026

O conector passou a usar a rota de senha ("Entrar", `index.php`) como padrão no formulário clássico do eproc. A rota "Certificado Digital" (`login_cert`) só é usada com `<PREFIX>_AUTH=a3`. O usuário do eproc é o CPF só com dígitos; o `.env` foi ajustado em `TJRJ_1G`, `TRF2_1G_RJ`, `TRF2_1G_ES`, `TJMG_1G` e `TJMS_1G` (os nomes `TJMS_EPROC_1G_*` viraram `TJMS_1G_*`).

| Portal | Login | Avisos | Processos | Processo (timeline) | Documento | Observação |
|---|---|---|---|---|---|---|
| `tjrj-eproc-1g` | ✅ | 1 prazo aberto; pendentes 0 | 4 | 3001751-83.2026.8.19.0024: 24 eventos | ✅ petição inicial, 19.902 caracteres | |
| `trf2-1g-rj` (JFRJ) | ✅ | 2 prazos abertos; pendentes 0 | 4 | 5073707-33.2026.4.02.5101: 23 eventos | ✅ petição inicial, ~30 mil caracteres | Antes da correção o conector mandava a senha pela rota do certificado |
| `trf2-1g-es` (JFES) | ✅ | tudo 0 | 3 | 5006345-17.2026.4.02.5003: 17 eventos | ✅ petição inicial, ~20 mil caracteres | idem |
| `tjms-eproc-1g` | ✅ | tudo 0 | 1 | 5000145-03.2026.8.12.0049: 28 eventos | ✅ petição inicial, ~29 mil caracteres | Entrou por senha, sem A3 |
| `tjmg-1g` | ✅ | 24 prazos abertos; 4 pendentes; pauta 1 | 50 | 1001391-96.2026.8.13.0042: 6 eventos | ✅ petição inicial, ~15 mil caracteres | O portal abre "Alterar Cadastro" depois do código; o conector segue para o painel sem enviar nada. Os 50 processos podem ser uma página só (conferir paginação). 3 logins hoje (2 para corrigir o reconhecimento do painel), todos aceitos |
| `trf6-1g` | ❌ | — | — | — | — | "Nome de usuário ou senha inválida" também com o CPF só dígitos. A cliente precisa confirmar a senha do eproc do TRF6 |
| `trf6-2g` | não tentado | — | — | — | — | Mesma credencial do 1G |

Nenhum documento de intimação pendente foi aberto. Nenhuma ciência, alteração de cadastro ou "liberar dispositivo".
