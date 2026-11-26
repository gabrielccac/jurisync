# Juliana — validação da lista enviada por ela (25/11/2026)

Um login novo por portal, só leitura, só com o `.env` dela, `acknowledgeCiencia=False`; nenhuma leitura registrou ciência.
Exceções: TJTO repetido a pedido (mais duas conexões de teste ao `mtls.tjto.jus.br`, sem login); TJES recomeçado depois
de a sessão cair no meio; TRF6 eproc reaberto depois da correção (na 1ª tentativa nada foi enviado).

| Portal | Login | Listas | Documento |
|---|---|---|---|
| tjmg-pje-1g | ✅ | Acervo 197, caixa 5 | ✅ |
| tjmg-pje-tr | ✅ | Acervo 29 | ✅ |
| tjmg-1g (eproc) | ✅ | 50 (1ª página), 8 pendentes | ✅ |
| tjmg-2g (eproc) | ✅ | 2 | ✅ |
| tjsp-esaj | ✅ | 1 | ⚠️ 2 processos pedem a senha do processo |
| tjrj-1g (PJe) | ✅ | Acervo 80, caixa 8 | ✅ |
| tjes-1g | ✅ CAPTCHA resolvido | Acervo 9, caixa 2 (2 sem ciência) | ✅ |
| tjsc-1g | ✅ | 24, 1 pendente | ✅ |
| tjsc-2g | ✅ | 0 | sem processo |
| tjrs-1g | ✅ (recusado em 23/11) | 8 | ✅ |
| tjpr-1g (eproc) | ✅ 2ª conta "Eproc/TJPR" | 2 | ✅ |
| tjpr-2g (eproc) | ✅ mesma 2ª conta | 0 | sem processo |
| tjba-1g | ✅ | Acervo 8 | ✅ |
| tjce-1g | ✅ | Acervo 19, caixa 1 | ✅ |
| tjpe-1g | ✅ | Acervo 12 | ✅ |
| tjpb-1g | ✅ | Acervo 9, caixa 2 | ✅ |
| tjpa-1g | ✅ | Acervo 8, caixa 6 | ✅ |
| tjdft-1g | ✅ | Acervo 9, caixa 1 | ✅ |
| tjms-esaj | ✅ | 1, 1 intimação sem ciência | ✅ (processo com intimação recusado pela trava) |
| tjal-esaj | ✅ | 5, 3 intimações sem ciência | ✅ |
| tjgo-1g (Projudi) | ✅ perfil MG144366 | 2 + 3, Diário 5 | ⚠️ lido num processo fixo do script, não da conta |
| tjam-esaj | ✅ | sem processo | — |
| trf6-1g (eproc) | ✅ A1 + QR | 8 | ✅ |
| trf6-2g (eproc) | ✅ | 0 | sem processo |
| trf6-pje-1g (configuração de teste) | ✅ A1 | Acervo 1 | ❌ número 4.01 recusado antes do envio; Push HTTP 302 |
| trf3-1g | ✅ | Acervo 1, caixa 1 | ✅ |
| trf1-1g | ✅ na 2ª tentativa, a pedido (na 1ª, tempo esgotado de 30 s) | Acervo 1, caixa 0 | ✅ |
| tjto-1g | ❌ `400 The SSL certificate error` no mtls (2 vezes; com e sem a cadeia; a mesma conexão, sem login, com o A1 de outros dois escritórios, de outras ACs, deu o mesmo erro) | — | — |

Sem CPF/senha, não testados: Projudi TJPR, TJRR (conector não ligado) e TJAM.
