# Portais novos: TRT23, TJDFT 2G, TJRS 2G, TRF6 PJe e Eproc TJBA

Explorados e validados em 25/11/2026 com o A1 de um advogado (só leitura, nenhuma ciência). Nenhum sai só do número do
processo quando outro sistema do mesmo tribunal usa a mesma região: aí vale o host do link ou o setup.

| Portal | Host | Login | Validado |
|---|---|---|---|
| `trt23-1g` | `pje.trt23.jus.br/primeirograu/` | SSO nacional, A1 + TOTP (`flavor="kz"`) | contadores, acervo, processo, documento |
| `trt23-2g` | `pje.trt23.jus.br/segundograu/` | idem | login aceito; conta sem cadastro (`not_registered`) |
| `tjdft-2g` | `pje2i.tjdft.jus.br/pje/` | SSO nacional (client `pje-tjdft-2g`), A1 + TOTP | contadores, caixa, acervo, processo, documento |
| `tjrs-2g` | `eproc2g.tjrs.jus.br/eproc/` | Keycloak do TJRS (pelo broker nacional), A1 + TOTP `Eproc/TJRS` | listas (conta sem processo no 2º grau) |
| `trf6-pje-1g` | `pje1g.trf6.jus.br/pje/` | A1 no desafio `ASSINATURA_MENSAGEM`, sem SSO e sem código | contadores, acervo, processo, documentos (HTML, PDF, OCR) |
| `trf6-pje-2g` | `pje2g.trf6.jus.br/pje/` | idem | conta sem cadastro (`not_registered`) |
| `tjba-eproc-1g` | `eproc1g.tjba.jus.br/eproc/` | formulário clássico + A1, como o TJMG | a conta não entrou: em 13/11 o portal não conhecia o CPF; em 25/11 o login não chegou ao painel |

Setup: `<PREFIX>_CERT=true` e, menos no TRF6 PJe, `<PREFIX>_2FA_QR`/`_2FA_DATA` com o perfil do QR. Prefixos: `TRT23_1G`,
`TRT23_2G`, `TJDFT_2G`, `TJRS_2G`, `TRF6_PJE_1G`, `TRF6_PJE_2G`, `TJBA_EPROC_1G`.

## Descobertas

- **TRF6 PJe** guarda os processos antigos, com número do TRF1 (`4.01`): `Portal.inherited` aceita essa região. O painel é
  o do PJe 2.x. O Push (`Push/listView.seam`) redireciona para `error.seam` com "Sem permissão para acessar a página":
  o tribunal não libera e `pje_list_watched_processes` diz isso; a lista é a do `pje_list_processes`.
- **TJRS 2G**: em 13/11 o teste parou no redirecionamento ao SSO nacional. Ele é só a passagem (broker) para o Keycloak
  do TJRS, o mesmo do 1º grau.
- **Eproc TJBA**: quando a resposta é "Não foi localizado usuário com este CPF", o erro do conector passa a ser
  `not_registered`. A mensagem é a registrada em 13/11; em 25/11 ela não foi conferida de novo.
- **Projudi TJMT** (`projudi.tjmt.jus.br`): 503 "Site em manutenção" em 13/11 e 25/11; nada para explorar.
