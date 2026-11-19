# Gonçalves Guimarães (Fernanda): validação completa em 19/11/2026

Testes ao vivo, só leitura, com o A1 do sócio e o TOTP do autenticador (`JURISYNC_CUSTOMER=fernanda`). Em cada portal: login, contadores/avisos, lista de processos, um processo com a linha do tempo e a leitura de um documento sem intimação pendente. Nenhuma ciência, nenhum ato de expediente aberto, nenhuma alteração de cadastro, "liberar dispositivo" nunca marcado. Os logins foram feitos em sequência, cada um com um código TOTP novo. Nenhuma senha, PIN, CPF, código ou nome de parte aparece aqui.

Resultado: **41 de 41 portais entram**. 34 leram um documento até o fim. Os outros 7 entram, mas não têm nenhum processo: TRF5 2G, TRF5 TRU, TJMG 2G, TJMS eproc e TRF6 2G (e TRT18 2G só tem processo arquivado, que foi lido).

## PJe (22)

| Portal | Login | Contadores/caixa | Processos | Processo | Documento |
|---|---|---|---|---|---|
| `trf1-1g` | ✅ | 3 pendentes | Push 20 | ✅ | ✅ |
| `trf3-1g` | ✅ | 3 pendentes | Push 35 | ✅ | ✅ |
| `trf3-2g` | ✅ | 0 | Push 1 | ✅ | ✅ |
| `trf5-1g` | ✅ | 3 pendentes | Push 12 | ✅ | ✅ |
| `trf5-2g` | ✅ | 0 | sem processos | — | — |
| `trf5-tru` | ✅ | 0 | sem processos | — | — |
| `tjrj-1g` | ✅ | 2 pendentes | processo da caixa | ✅ | ✅ |
| `trt1-1g` | ✅ | Meus Expedientes 6, Acervo 23 | Push 30 | ✅ | ✅ |
| `trt1-2g` | ✅ | Acervo 1 | Push 1 | ✅ | ✅ |
| `trt2-1g` | ✅ | ME 5, Acervo 16 | processo da caixa | ✅ | ✅ |
| `trt2-2g` | ✅ | Acervo 1 | Acervo Geral | ✅ | ✅ |
| `trt3-1g` | ✅ | ME 3, Acervo 14 | processo da caixa | ✅ | ✅ |
| `trt3-2g` | ✅ | Acervo 2 | Acervo Geral | ✅ | ✅ (PDF escaneado: precisa de OCR) |
| `trt4-1g` | ✅ | ME 6, Acervo 21 | processo da caixa | ✅ | ✅ |
| `trt9-1g` | ✅ | ME 7, Acervo 20 | Push 31 | ✅ | ✅ |
| `trt12-1g` | ✅ | ME 31, Acervo 160 | Push 292 | ✅ | ✅ |
| `trt12-2g` | ✅ | ME 4, Acervo 13 | Push 28 | ✅ | ✅ |
| `trt15-1g` | ✅ | ME 4, Acervo 36 | Push 42 | ✅ | ✅ |
| `trt15-2g` | ✅ | Acervo 1 | Push 4 | ✅ | ✅ (PDF escaneado: precisa de OCR) |
| `trt18-1g` | ✅ | Acervo 1 | Push 3 | ✅ | ✅ |
| `trt18-2g` | ✅ | só Arquivados 1 | Arquivados | ✅ | ✅ |
| `tst` | ✅ | ME 1, Acervo 3 | Push 7 | ✅ | ✅ |

## Eproc (19)

| Portal | Login | Avisos (pend. / urg. / prazo aberto) | Processos | Processo | Documento |
|---|---|---|---|---|---|
| `tjsc-1g` | ✅ | 25 / 1 / 189 | 843 | ✅ | ✅ |
| `tjsc-2g` | ✅ | 2 / 0 / 24 | 122 | ✅ | ✅ |
| `trf4-1g-sc` | ✅ | 3 / 0 / 31 | 159 | ✅ | ✅ |
| `trf4-1g-rs` | ✅ | 0 / 0 / 2 | 8 | ✅ | ✅ |
| `trf4-1g-pr` | ✅ | 0 / 0 / 2 | 15 | ✅ | ✅ |
| `trf4-2g` | ✅ | 0 / 0 / 1 | 32 | ✅ | ✅ |
| `tjrs-1g` | ✅ | 0 / 0 / 12 | 32 | ✅ | ✅ (PDF escaneado: precisa de OCR) |
| `tjsp-1g` | ✅ | 1 / 0 / 4 | 14 | ✅ | ✅ |
| `tjsp-2g` | ✅ | 0 | 3 | ✅ | ✅ |
| `tjrj-eproc-1g` | ✅ | 0 | 5 | ✅ | ✅ |
| `tjpr-1g` | ✅ | 0 | 1 | ✅ | ✅ |
| `trf2-1g-rj` | ✅ | 1 / 0 / 1 | 10 | ✅ | ✅ |
| `trf2-1g-es` | ✅ | 0 / 0 / 2 | 3 | ✅ | ✅ |
| `trf2-2g` | ✅ | 0 | 2 | ✅ | ✅ |
| `tjmg-1g` | ✅ | 0 / 0 / 2 | 3 | ✅ | ✅ |
| `tjmg-2g` | ✅ | 0 | sem processos | — | — |
| `tjms-eproc-1g` | ✅ | 0 | sem processos | — | — |
| `trf6-1g` | ✅ | 0 / 0 / 3 | 6 | ✅ | ✅ |
| `trf6-2g` | ✅ | 0 | sem processos | — | — |

## Corrigido no conector nesta rodada

- **TRF6 com A1:** o navegador só passa o bloqueio anti-robô da entrada; o certificado e o código seguem por HTTP. Antes só entrava com uma montagem manual.
- **TJSC:** documento ligado a dois eventos fazia a leitura falhar.
- **Listas paginadas** ("843 registros - 1 a 500") vinham marcadas como completas (TJSC 1G, TRF4 SC). Agora dizem que estão incompletas.
- **PJe:** Push vazio vinha como "incompleto"; agora é uma lista completa com zero processos.

O `portals.toml` agora fixa `<PREFIX>_CERT=true` em todos os portais e o perfil PDPJ (índice 7) nos PJe.
