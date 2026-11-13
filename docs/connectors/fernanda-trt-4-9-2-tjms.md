# Cobertura Fernanda — TRT4, TRT9, TRT2 e eproc TJMS

Validação de 13/11/2026 com o A1 do José e somente os perfis dele no QR1. Branch `feat/trt-4-9-2-tjms`. Base local preservada no commit separado `5bc9c42` (`base: snapshot`); nenhum push.

## Resultado por ambiente

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

## Implementação

- Novos registros: `trt4-1g`, `trt9-1g`, `trt2-1g`, `trt2-2g`, todos PJe com `justice="5"`, região CNJ correta e `flavor="kz"`. Reutilizam os leitores KZ de contadores, expedientes, processos e documentos.
- Novo registro `tjms-eproc-1g`: A1 no formulário clássico, broker mTLS `https://mtls.tjms.jus.br/priv/login_cert.php`, retorno e TOTP local. O host mTLS é permitido só para esse portal e esse caminho. O login não envia `chkLiberarDispositivo`, inclusive se ele aparecer como campo oculto.
- TJMS mantém coexistência com e-SAJ: eproc não é inferido por número CNJ, evitando redirecionar processos antigos do e-SAJ para o novo sistema.
- PJe: `errorUnexpected.seam` passa a ser erro explícito, impedindo que a presença do texto genérico “Painel do advogado” nessa página seja tratada como autenticação.
- Testes cobrem cadastro/roteamento, host de certificado, formulário local e omissão da liberação de dispositivo; os testes existentes de KZ e autenticação continuam sendo usados.

## Limites das evidências

Os quatro ambientes TRT aprovados passaram por A1 + TOTP, token com a instância correta, contadores, amostra da caixa, leitura de um processo e um PDF com extração. TRT2 2G tinha zero expedientes na caixa consultada; a amostra de processo veio do acervo. Nenhum ato pendente de ciência foi aberto: o leitor verificou os expedientes antes do PDF, com `acknowledge_ciencia=False` e `save=False`.

TRT4 2G redirecionou ao pré-cadastro depois do SSO. TRT9 2G informou literalmente: “Erro inesperado, por favor tente novamente. Erro: Não existe localização/perfil para o usuário informado. (SJT-API-039)”. Ambos aceitam o A1 no SSO; esses resultados não são recusa do certificado. Não foram cadastrados como cobertura validada nesta branch.

No TJMS 1G foram confirmados painel, filas e listagem pelo leitor existente. A quantidade de itens da listagem não foi preservada corretamente na sondagem; não é possível afirmar que não existam processos. Processo específico e documentos não foram validados. O helper de login incorporado reproduz o fluxo HTTP observado e foi verificado por teste com respostas simuladas; não houve login adicional após sua implementação, pois as três sondagens reservadas ao portal já tinham sido usadas.

TJMS 2G permanece fora: o broker retorna a autenticação ao host de 1G; o guard recusou essa troca de instância. As duas primeiras sondagens por grau pararam no guard antes de visitar o broker; a terceira chegou ao mTLS. O limite não foi ampliado. Não há implementação de 2G baseada em tentativa de adivinhar o retorno.

## Configuração e integração

`customers/fernanda/portals.toml` desta branch é um **incremento de cinco blocos**, a mesclar com o cadastro completo produzido pelas outras frentes. Não substituir o arquivo completo da cliente por esse incremento.

Somente 20 variáveis novas foram acrescentadas ao `.env` original da Fernanda: CERT, 2FA_DATA, 2FA_QR e OTP_PROFILE_INDEX para cada novo registro. Nenhum valor existente substituído. Os TRTs selecionam PDPJ no índice 7 do QR1; TJMS seleciona Eproc/TJMS no índice 5. O `.env`, o A1 e os QRs não foram copiados para o worktree nem adicionados ao Git.

Não testados nesta frente: TRT12, TRT15, TRT1, TRT3, TRT18 e os demais portais existentes, reservados aos outros executores. Sem CAPTCHA ou verificação humana nos fluxos desta frente. Nenhuma petição, ciência, alteração de cadastro, senha, perfil padrão ou dispositivo liberado.

SHA-256 do PFX antes/depois: `26b0d8213b3c491ce9b68fd973c669b46a983c2191b9b0c54e2af4376e8c3f75`. PEMs temporários usados pelo mTLS são privados e removidos pelo contexto do conector, inclusive quando ele lança erro. As sondagens não gravaram cookies ou conteúdo dos processos/documentos.

## Testes finais

`pytest tests/core/ tests/connectors/pje/ tests/connectors/eproc/`: **461 passed, 1 skipped, 164 subtests passed**. Ruff lint/format e `git diff --check` aprovados. Configuração dos cinco novos portais e seleção dos perfis também verificadas offline.
