# Progresso TRT (worktree feat/trt-12-15-1-3)

Harness temporario fora do repo (/tmp/trtwork). Segredos so no ambiente do processo.

## TRT12 - concluido
- trt12-1g: login OK (A1 + TOTP; 3 logins no total, 2 por bug do meu script, nenhum falhou). counters OK (Acervo Geral 158, Meus Expedientes 27, Arquivados 144), caixa Meus Expedientes OK (10 caixas, completa), process read OK (36 expedientes completos), document read OK (PDF, texto extraido). Documento de expediente sem ciencia: PendingNotice (trava de seguranca, correto).
- trt12-2g: login OK (1 tentativa). counters OK (11/4/32), inbox OK, process OK, document OK.
- Cadastrado: sim (commit "TRT12").

## TRT15 - concluido
- trt15-1g: login OK (1 tentativa). counters 36/4/5, inbox OK (4 caixas), process OK, document OK.
- trt15-2g: login OK (1 tentativa). counters 2/1/2, inbox OK, process OK, document OK.
- Cadastrado: sim.

## TRT1 - concluido
- trt1-1g: login OK (1). counters 22/3/7, inbox OK (3 caixas), process OK (17 expedientes), document OK.
- trt1-2g: login OK (2 tentativas, ambas entraram; a 2a foi para ler processo pelo Acervo, pois Meus Expedientes estava vazia). counters 1/0/0, inbox OK (vazia), process e document OK via Acervo.
- Cadastrado: sim.

## TRT3 - concluido
- trt3-1g: login OK (1). counters 14/4/7, inbox OK (4 caixas), process OK (23 expedientes), document OK.
- trt3-2g: login OK (1). counters 2/0/1, inbox OK (vazia), process e document OK via Acervo.
- Cadastrado: sim.

# Cobertura Fernanda — TRT4, TRT9, TRT2 e TJMS

Branch: `feat/trt-4-9-2-tjms`. Worktree isolado, sem push.

Status: exploração concluída. Quatro ambientes TRT com leituras até PDF; TJMS 1G com login, filas e listagem; três ambientes sem acesso completo. Integração e testes locais concluídos; sem push.

| Portal | Login | Leituras | Tentativas |
|---|---|---|---|
| trt4-1g | ✅ A1 + TOTP; sessão autenticada PJe-KZ | contadores: OK (3 grupos); expedientes: OK (3 amostra); processo: OK (52 movimentos; 44 documentos); documento: HTTP 200; 71558 bytes; extracted | 1 |
| trt4-2g | ❌ trt4-2g: the SSO login worked but this user is not registered in this PJe (redirected to its pre-registration page); the court must register the lawyer first | Não executadas | 1 |
| trt9-1g | ✅ A1 + TOTP; sessão autenticada PJe-KZ | contadores: OK (3 grupos); expedientes: OK (3 amostra); processo: OK (20 movimentos; 19 documentos); documento: HTTP 200; 66124 bytes; extracted | 1 |
| trt9-2g | ⛔ Erro inesperado, por favor tente novamente. Erro: Não existe localização/perfil para o usuário informado. (SJT-API-039) | Não executadas | 3 |
| trt2-1g | ✅ A1 + TOTP; sessão autenticada PJe-KZ | contadores: OK (3 grupos); expedientes: OK (1 amostra); processo: OK (248 movimentos; 204 documentos); documento: HTTP 200; 66115 bytes; extracted | 1 |
| trt2-2g | ✅ A1 + TOTP; sessão autenticada PJe-KZ | contadores: OK (3 grupos); expedientes: OK (0 amostra); acervo: OK (1 amostra); processo: OK (73 movimentos; 107 documentos); documento: HTTP 200; 66102 bytes; extracted | 1 |
| tjms-eproc-1g | ✅ A1 mTLS + TOTP; painel do advogado | painel: OK; pending: OK; open_deadlines: OK; processos: OK; processo/documento: Não validados; quantidade da listagem não foi registrada | 3 |
| tjms-eproc-2g | ⛔ Broker mtls.tjms.jus.br devolve o callback ao eproc1g; o fluxo do 2G não foi concluído | Não executadas | 3 |

Somente login e leitura, sem ciência nem alteração de conta. CAPTCHA/verificação humana: parar e registrar que precisa de uma pessoa. Limite: três tentativas completas de login por portal, sem laço automático. Perfis exclusivamente do José. Nenhum segredo neste arquivo.

SHA-256 do PFX antes dos testes: `26b0d8213b3c491ce9b68fd973c669b46a983c2191b9b0c54e2af4376e8c3f75`.

## Notas de evidência

- TRT4 2G: redirecionamento pós-SSO ao pré-cadastro; o conector informa `the SSO login worked but this user is not registered in this PJe`. Não houve recusa do A1.
- TRT9 2G: A1 e TOTP aceitos pelo SSO, seguido de `/segundograu/errorUnexpected.seam` e erro literal `Não existe localização/perfil para o usuário informado. (SJT-API-039)`. A API de token devolve HTTP 401. Mantido como trava, sem classificar como certificado recusado.
- TJMS: as duas primeiras sondagens por grau pararam na validação do destino `mtls.tjms.jus.br/priv/login_cert.php`, antes de acessá-lo. A terceira fez o mTLS. No 1G, o formulário TOTP não enviou `chkLiberarDispositivo`; no 2G o retorno foi para o host de 1G, bloqueado pela vinculação de origem. Nenhuma nova tentativa depois do limite.
- TJMS 1G: painel, filas e relação de processos responderam. A quantidade de processos não foi preservada corretamente na sondagem, portanto não afirmo que a conta esteja vazia. Processo específico e documentos não foram validados.
- Não houve CAPTCHA/verificação humana nesta frente. Nenhuma ciência, petição, mudança de senha, perfil padrão ou dispositivo liberado.
- Quatro PDFs TRT foram lidos sem salvar os arquivos; somente documentos cuja verificação de expedientes comprovou ausência de ciência pendente. Conteúdo e números dos processos não foram registrados no relatório.
- Cadastro incremental: `trt4-1g`, `trt9-1g`, `trt2-1g`, `trt2-2g`, `tjms-eproc-1g`. O cadastro público desta branch contém somente esses cinco blocos e deve ser mesclado ao arquivo completo da Fernanda, preservando os blocos das outras frentes.
- Foram acrescentadas 20 variáveis novas ao `.env` original da Fernanda (CERT, 2FA_DATA, 2FA_QR, OTP_PROFILE_INDEX); nenhum valor anterior alterado. Perfil PDPJ no índice 7 do QR1 para TRTs e Eproc/TJMS no índice 5 do QR1. Nenhum segredo copiado para o worktree ou commit.

## Verificação final

- `pytest tests/core/ tests/connectors/pje/ tests/connectors/eproc/`: 461 passed, 1 skipped, 164 subtests passed.
- Ruff lint e formatação dos arquivos alterados: OK.
- Cadastro incremental: cinco portais sem variáveis ausentes e seleção dos perfis conferida offline.
- SHA-256 do PFX preservado após todos os testes. Scripts temporários, resultados sanitizados e caches desta execução removidos; PEMs descartados pelo contexto de mTLS.
