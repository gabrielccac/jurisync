# Juliana — validação ao vivo em 23/11/2026

Cadeia por portal: login, filas/contadores, lista de processos ou caixa, um processo, um documento. Só leitura, sem ciência.

Branch `main` (fdc2a1e), checkout principal, sem alterar código, `.env`, `portals.toml` ou certificado. Funções dos conectores chamadas direto em Python, **um processo e um login por portal**, um portal por vez. Os PJe rodaram com 40 s de intervalo entre si (conta PDPJ única). Nenhum segredo foi impresso ou registrado. Nenhuma ciência, nenhum protocolo, nenhum "liberar dispositivo" nem "definir como padrão".

**Como a ciência foi protegida.**
- **Eproc:** antes de abrir qualquer documento, o processo escolhido tinha de estar fora das filas pendentes (`pending`, `pending_urgent`, `pending_agreement`, `art334`, lidas antes). Depois, `client.pending_notices` voltava vazio e sem problema, e a linha do tempo não tinha evento "AGUARD. ABERTURA". Nenhuma leitura voltou com bloco `ciencia`.
- **PJe:** `pje_read_documents` com `acknowledgeCiencia=False` explícito. A trava do conector não recusou nada.
- **Projudi:** `read_documents` com `acknowledge_ciencia=False` (trava do conector).
- **e-SAJ:** documentos só fora de processo com intimação sem ciência. Nesta execução nenhum documento chegou a ser aberto.

Documentos lidos: sempre a petição inicial (peça da própria advogada). No PJe o texto foi cortado em 2.000 caracteres (`textLimit=2000`), por isso aparece "≥2.000".

## Eproc (A1 + autenticador do tribunal)

Filas = pendentes / prazo aberto. As demais filas pendentes (urgente, acordo, art. 334) deram 0 em todos os portais.

| Portal | Login | Filas | Lista de processos | Processo | Documento | Observação |
|---|---|---|---|---|---|---|
| tjmg-1g | ✅ 1 | ✅ 13 / 53 | ✅ 50 de 143 (1ª página) | ✅ 1000050-00.2026.8.13.0021 (16 ev., 23 docs) | ✅ Petição inicial, 33.256 chars | |
| tjmg-2g | ✅ 1 | ✅ 0 / 0 | ✅ 2 | ✅ 5200051-00.2023.8.13.0024 (5 ev., 0 docs) e 5200052-00.2024.8.13.0024 (22 ev., 6 docs) | — não lido | Não há petição inicial no 2G (há Réplica). O filtro do meu script só aceitava "inicial/petição", então não leu. Não houve 2º login (regra). |
| tjsp-1g | ⚠️ 1ª: ❌ `LoginRejected: eproc asks which profile to use (MG144366 / ADVOGADO, RJ269531 / ADVOGADO); set TJSP_1G_USER_PROFILE to one of them`. 2ª, com `TJSP_1G_USER_PROFILE=MG144366` só no ambiente do processo: ✅ | ✅ 1 / 28 | ✅ 50 de 52 | ✅ 4000053-00.2026.8.26.0024 (7 ev., 26 docs) | ✅ Petição inicial, 39.368 chars | Falta configurar o perfil (ver abaixo) |
| tjsp-2g | ✅ 1 (com `TJSP_2G_USER_PROFILE=MG144366` no processo) | ✅ 0 / 0 | ✅ 0 | — | — | Não há processo no 2G |
| tjsc-1g | ✅ 1 | ✅ 1 / 7 | ✅ 24 (completa) | ✅ 5000054-00.2026.8.24.0038 (9 ev., 20 docs) | ✅ Petição inicial, 31.338 chars | |
| tjsc-2g | ✅ 1 | ✅ 0 / 0 | ✅ 0 | — | — | Não há processo |
| tjrj-eproc-1g | ✅ 1 | ✅ 0 / 2 | ✅ 9 (completa) | ✅ 3000055-00.2026.8.19.0001 (100 ev., 48 docs) | ✅ Petição inicial, 37.645 chars | |
| tjrj-eproc-2g | ✅ 1 | ✅ 0 / 3 | ✅ 1 | ✅ 3000056-00.2026.8.19.0001 (20 ev., 4 docs: relatório/voto, acórdão, extrato de ata) | — não lido | Não há petição no inventário do 2G (mesmo filtro do tjmg-2g) |
| tjrs-1g | ❌ `LoginRejected: eproc did not accept the A1 certificate` | — | — | — | — | O portal voltou para o login com `?erro` depois do POST do certificado. O TOTP não chegou a ser pedido. Não houve nova tentativa. |
| tjms-eproc-1g | ✅ 1 | ✅ 0 / 0 | ✅ 3 | ✅ 5000057-00.2026.8.12.0001 (22 ev., 27 docs) | ✅ Petição inicial, 20.073 chars | |
| tjpr-1g | ❌ `LoginRejected: eproc rejected the OTP` | — | — | — | — | O A1 passou e o código foi recusado. Não houve nova tentativa. |
| tjpr-2g | não tentado | — | — | — | — | De propósito: o 1G recusou o código da 1ª conta "Eproc/TJPR" |
| tjac-1g | ✅ 1 | ✅ 0 / 0 | ✅ 1 | ✅ 5000058-00.2026.8.01.0001 (14 ev., 19 docs) | ✅ Petição inicial, 40.144 chars | |
| tjac-2g | ✅ 1 | ✅ 0 / 0 | ✅ 0 | — | — | Não há processo |

## PJe (A1 + PDPJ)

Contadores = buckets do painel na ordem do portal: Pendentes de ciência ou de resposta / Apenas pendentes de ciência / Ciência pelo destinatário / Ciência pelo Judiciário / Prazo findo em 10 dias / Sem prazo / Respondidos em 10 dias (/ 8º bucket onde existe). Caixa = bucket 0, 1 página. O código PDPJ nunca foi recusado. Foram 16 logins, cada um com 40 s de intervalo.

| Portal | Login | Contadores | Caixa | Push | Processo | Documento | Observação |
|---|---|---|---|---|---|---|---|
| tjmg-pje-1g | ✅ | ✅ 7/0/0/7/3/0/0 | ✅ 7 linhas, 6 caixas | ❌ `Could not uniquely identify the PJe Push process grid` | — | — | Meu script só pegava processos do Push e do DJEN, não os da caixa. Com o Push quebrado, nenhum processo foi aberto. Não houve 2º login. |
| tjmg-pje-tr | ✅ | ✅ todos 0 | ✅ 0 | ❌ mesmo erro do Push | ❌ 2 números do DJEN-TJMG: `Process not found in the search nor in the lawyer's Acervo` | — | Os números do DJEN são de outro sistema ou instância. Não há processo dela visível na Turma Recursal. |
| tjrj-1g | ✅ | ✅ 9/0/0/6/1/3/0 | ✅ 9 linhas, 8 caixas | ✅ 75 | ✅ 0800059-00.2026.8.19.0031 (12 docs, 1 expediente) | ✅ Petição Inicial, ≥2.000 chars | |
| tjce-1g | ✅ | ✅ 1/0/0/0/0/1/1 | ✅ 1 linha | ✅ 14 | ✅ 3000060-00.2025.8.06.0013 (85 docs, 16 exp., 2 abertos) | ✅ Petição Inicial, ≥2.000 | |
| tjpb-1g | ✅ | ✅ 2/0/0/1/0/1/0 | ✅ 2 linhas | ✅ 6 | ✅ 0800061-00.2025.8.15.0001 (38 docs) | ✅ Petição Inicial, ≥2.000 | |
| tjmt-1g | ✅ | ✅ 1/0/0/1/0/0/0/1 | ✅ 1 linha | ✅ 2 | ✅ 1000062-00.2025.8.11.0015 (42 docs) | ✅ Petição Inicial (217877405), ≥2.000 | O 404 da Hérica no TJMT não se repetiu aqui |
| tjmt-2g | ✅ | ✅ 1/0/0/1/0/0/0/1 | ✅ 1 linha | ✅ 1 | ✅ 1000063-00.2025.8.11.0004 (68 docs, 2 abertos) | ✅ Petição Inicial (394314423), ≥2.000 | |
| tjdft-1g | ✅ | ✅ 1/0/0/0/0/1/0 | ✅ 1 linha | ✅ 9 | ✅ 0700064-00.2025.8.07.0004 (48 docs) | ✅ Petição Inicial, ≥2.000 | |
| tjes-1g | ✅ CAPTCHA do WAF resolvido (18 s) | ✅ 0/0/0/0/1/0/0 | ✅ 0 | ❌ `Could not uniquely identify the PJe Push process grid` | ✅ 5000065-00.2026.8.08.0024, tirado do DJEN (11 docs, 2 exp., 1 aberto) | ✅ Petição Inicial, ≥2.000 | A trava não recusou: a inicial não é ato de expediente pendente |
| tjap-1g | ✅ | ✅ 0/0/0/0/1/0/0 | ✅ 0 | ✅ 4 | ✅ 6000066-00.2025.8.03.0001 (80 docs) | ✅ Petição Inicial, ≥2.000 | |
| tjpe-1g | ✅ | ✅ todos 0 | ✅ 0 | ✅ 9 | ✅ 0000067-00.2025.8.17.8201 (47 docs) | ✅ Petição Inicial (Outras), ≥2.000 | |
| tjma-1g | ✅ | ✅ 1/0/0/1/1/0/0/0 | ✅ 1 linha | ✅ 8 | ✅ 0800068-00.2025.8.10.0007 (35 docs) | ✅ Petição Inicial, ≥2.000 | |
| tjpa-1g | ✅ | ✅ 6/0/0/0/0/6/0 | ✅ 6 linhas, 4 caixas | ❌ `Could not uniquely identify the PJe Push process grid` | ✅ 0800069-00.2025.8.14.0301 e 0800070-00.2026.8.14.0136 (aba Expedientes completa, ao contrário da Hérica) | ⚠️ 1º processo: `Selected document has no download location` (151361043). 2º: ✅ Petição Inicial, ≥2.000 | Processos tirados da caixa |
| tjba-1g | ✅ | ✅ 1/0/0/0/1/0/0/0 | ✅ 1 linha | ✅ 9 | ✅ 8000071-00.2025.8.05.0080 (18 docs) | ❌ Petição Inicial 507664773: `extractionStatus=request_failed`, 0 chars | O download falhou. Não houve nova tentativa. |
| tjro-1g | ✅ | ✅ todos 0 | ✅ 0 | ✅ 2 | ✅ 7000072-00.2024.8.22.0000 (71 docs) | ✅ PETIÇÃO INICIAL, ≥2.000 | |
| trf3-1g | ✅ | ✅ 1/0/0/1/0/0/0/0 | ✅ 1 linha | ✅ 1 | ✅ 5000073-00.2025.4.03.6336 (79 docs, 5 abertos) | ✅ Petição inicial, ≥2.000 | |

## Projudi TJGO e e-SAJ (só A1)

| Portal | Login | Filas | Lista | Processo | Documento | Observação |
|---|---|---|---|---|---|---|
| tjgo-1g | ⚠️ 1ª: ❌ `LoginRejected: Projudi asks which serventia to use (OAB MG 144366-A / OAB RJ 269531-S); set TJGO_1G_USER_PROFILE to one of them`. 2ª, com `TJGO_1G_USER_PROFILE=MG144366` só no processo: ✅ | ✅ Diário 1 (declarado 1), pendências do portal 0 (lista completa) | ✅ JEC 2 + UPJ dos JECs 3 (completa) | ✅ 5800074-00.2025.8.09.0025 (190 movimentações, 91 arquivos) | ✅ `peticaoinicial11.pdf`, ≥60.000 chars | Nenhuma intimação foi aberta. Só listas. |
| tjms-esaj | ✅ 1 (`usuarioLogado`, 3 OABs) | ✅ intimações 1 (1 sem ciência), audiências 0, petições 1 aguardando assinatura / 0 concluídas, caixa postal 3 | — | — | — | Os itens de petição não trouxeram número CNJ formatado e meu script não achou processo (ver abaixo) |
| tjal-esaj | ✅ 1 (`usuarioLogado`, 3 OABs) | ✅ intimações 3 (todas sem ciência), audiências 1, petições 1 aguardando / 4 concluídas, caixa postal 17 | — | — | — | Mesmo caso do TJMS |

## Resumo

- **33 portais configurados, 32 tentados.** O tjpr-2g ficou de fora de propósito.
- **Logins OK: 30.** Falharam o tjrs-1g (certificado recusado) e o tjpr-1g (código recusado). O tjsp-1g e o tjgo-1g precisaram de um 2º login, porque a 1ª tentativa parou na escolha de perfil (configuração nossa, não recusa do portal).
- **Documento lido: 20.** Foram 6 eproc (tjmg-1g, tjsp-1g, tjsc-1g, tjrj-eproc-1g, tjms-eproc-1g e tjac-1g), 1 Projudi (tjgo-1g) e 13 PJe.
- **Sem documento:**
  - portais sem processo: tjsp-2g, tjsc-2g e tjac-2g;
  - limite do meu script, sem 2º login: tjmg-2g, tjrj-eproc-2g, tjmg-pje-1g, tjms-esaj e tjal-esaj;
  - tjmg-pje-tr: nenhum processo encontrado;
  - tjba-1g: o download do documento falhou.
- O código PDPJ nunca foi recusado, e nenhuma ciência foi registrada.

## Problemas encontrados

1. **Perfil não configurado (TJSP eproc e TJGO Projudi).** O mesmo A1 tem dois perfis de advogada, OAB MG 144366 e OAB RJ 269531. A OAB SP 552800 não aparece no eproc TJSP. Sem `<PREFIX>_USER_PROFILE`, o login para e pede a escolha. Falta no `[env].set` do `customers/juliana/portals.toml`: `TJSP_1G_USER_PROFILE`, `TJSP_2G_USER_PROFILE` e `TJGO_1G_USER_PROFILE` = "MG144366" (valor usado nesta validação; confirmar com a advogada se o perfil certo é o de MG). Os outros eproc entraram sem essa variável. Mesmo assim, o perfil RJ pode aparecer em outros portais no futuro.
2. **Push do PJe:** `Could not uniquely identify the PJe Push process grid` no tjmg-pje-1g, no tjmg-pje-tr, no tjes-1g e no tjpa-1g (`push.py::parse_push_items`; no TJPA já tinha aparecido na Hérica).
3. **TJBA 1G:** o download da petição inicial 507664773 do processo 8000071-00.2025.8.05.0080 deu `request_failed`. Precisa de captura (em 17/11 o TJBA funcionava para a Hérica).
4. **TJPA 1G:** o `documentInventory` traz o id 151361043 ("Petição Inicial") sem `download_url`, e a leitura falha com `Selected document has no download location`. Pode ser o mesmo problema de inventário visto na Hérica.
5. **e-SAJ TJMS/TJAL:** não foi possível escolher um processo. As listas de petições (aguardando assinatura/concluídas) não trouxeram número CNJ com máscara, e as intimações estão todas sem ciência, o que as exclui. Na próxima rodada, use o número do DJEN ou de `nuProcesso` sem máscara.

## O que o escritório precisa fazer / fornecer

- **TJRS eproc:** o tribunal não aceitou o certificado A1 dela. Peça à advogada para verificar se ela tem cadastro no eproc do TJRS (OAB MG como inscrição suplementar/advogada de outro estado) e se o certificado está vinculado ao cadastro. Uma alternativa é fornecer usuário e senha do eproc TJRS. Não tentar de novo antes disso.
- **TJPR eproc:** o código da 1ª conta "Eproc/TJPR" do autenticador foi recusado. Pergunte à advogada qual das duas contas "Eproc/TJPR" é a do 1º grau e qual é a do 2º, ou peça um novo QR do TJPR. O `TJPR_1G_2FA_DATA`/`TJPR_*_OTP_PROFILE_INDEX` deve ser ajustado só depois dessa resposta. O tjpr-2g não foi testado.
- **TJSP eproc e TJGO Projudi:** confirmar que o perfil de trabalho é a OAB MG 144366. Depois incluir o `*_USER_PROFILE` no `[env].set` (ajuste nosso, a advogada não precisa fazer nada).
- **TJMS/TJAL e-SAJ:** há intimações sem ciência no painel (TJMS 1, TJAL 3). O escritório decide quando dar ciência; o conector não as abre.
- **Pendentes de antes** (fora desta validação): eproc TRF6 e TJTO e Projudi TJPR e TJRR precisam de usuário e senha. Eproc TJAL e TJSE e Projudi TJBA não têm conector.
