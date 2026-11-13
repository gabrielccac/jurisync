# PJe estaduais: TJPI, TJRN e TJRO

Validado em 13/11/2026 com o A1 e-CPF de uma advogada, mais o TOTP do perfil PDPJ (somente login e leitura).

| Portal | Chave | Host | Raiz | Prefixo de ambiente |
|---|---|---|---|---|
| TJPI 1G / 2G | `tjpi-1g`, `tjpi-2g` | `pje.tjpi.jus.br` | `/1g/`, `/2g/` | `TJPI_1G`, `TJPI_2G` |
| TJRN 1G / 2G | `tjrn-1g`, `tjrn-2g` | `pje1g.tjrn.jus.br`, `pje2g.tjrn.jus.br` | `/pje/` | `TJRN_1G`, `TJRN_2G` |
| TJRO 1G / 2G | `tjro-1g`, `tjro-2g` | `pjepg.tjro.jus.br`, `pjesg.tjro.jus.br` | `/pje/` | `TJRO_1G`, `TJRO_2G` |

Todos usam `certificate_totp` (SSO `sso.cloud.pje.jus.br`, clients `pje-tjro-1g`/`pje-tjro-2g` no TJRO). Ative com
`<PREFIX>_CERT=true`, `<PREFIX>_2FA_QR` e `<PREFIX>_OTP_PROFILE_MATCH` (perfil do TOTP, ex. PDPJ); o A1 vem de
`PJE_A1_P12`/`PJE_A1_PIN`.

## Descobertas

- TJRO: `pje.tjro.jus.br` só redireciona para a página institucional; o PJe 1º grau é `pjepg` e o 2º grau é `pjesg`.
  `pje1g`/`pje2g.tjro.jus.br` não existem no DNS.
- TJRN: a Akamai devolve 403 a `curl` simples; o conector (impersonação TLS) entra normalmente.
- TJPI: a tabela de resultado da pesquisa de processo tem 8 colunas (o número do processo é a 1ª célula, sem coluna
  de ações). `parsers.parse_search_results` agora aceita as duas formas.
- TJRN 1G: a pesquisa de processo por número exige reCAPTCHA invisível ("A verificação de captcha não está correta").
  Não é contornada. O processo abre pelo link `ca` da própria caixa de expedientes (a 2G pesquisa normalmente).
  Esse caminho ainda não está no conector: `fetch_process` por número falha no `tjrn-1g` até ser implementado.
- TJPI 2G com advogada sem cadastro cai em "Cadastro de novo usuário": é pendência de cadastro, não falha do portal.
