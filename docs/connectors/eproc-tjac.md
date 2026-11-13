# eproc TJAC (1º e 2º grau)

Portais `tjac-1g` (`eproc1g.tjac.jus.br`) e `tjac-2g` (`eproc2g.tjac.jus.br`), ambos em `/eproc/`, CNJ J=8 TR=01.

## Login

A raiz do eproc redireciona ao Keycloak próprio do tribunal (`sso.tjac.jus.br`, realm `eproc`). O botão de certificado
leva a `certificado-sso.tjac.jus.br`, que pede o A1 por mTLS; depois vem o código do autenticador (TOTP). É o fluxo de
`connectors/eproc/certificate.py` (`cert_login`), sem mudança de protocolo:

1. A1 por mTLS em `certificado-sso.tjac.jus.br` -> formulário `kc-x509-login-info`;
2. `kc-otp-login-form` com o TOTP;
3. painel do advogado (`acao=painel_adv_listar`).

Peculiaridade: o SSO devolve o navegador a `/controlador.php` (sem `/eproc/`), embora o resto do sistema esteja em
`/eproc/`. `cert_login` agora devolve a URL do painel reposta sob a raiz (`certificate.rooted`); sem isso os links
relativos das filas saíam do prefixo que `require_route` exige.

Perfil do advogado: com o A1 da Hérica não apareceu escolha de perfil (entrou direto no painel, perfil
`ADVOGADO`).

## Configuração do cliente

- `TJAC_1G_CERT=true` / `TJAC_2G_CERT=true` (A1 em vez de usuário/senha) com `PJE_A1_P12` e `PJE_A1_PIN`.
- 2FA: o perfil do autenticador é um só, `Eproc/TJAC`, para os dois graus. Os segredos podem usar o prefixo
  `TJAC_EPROC_` (`TJAC_EPROC_2FA_QR` / `TJAC_EPROC_2FA_DATA`), aceito como prefixo legado.

## Validação ao vivo (13/11/2026, só leitura)

| Leitura | 1g | 2g |
|---|---|---|
| login A1 + TOTP até o painel | ok | ok |
| fila `pending` | ok (0 itens) | ok (0 itens) |
| fila `open_deadlines` | ok (4 itens) | ok (0 itens) |
| relatório de processos | ok (33 itens na 1ª página) | ok (10 itens) |
| processo (`fetch_process`, sem abrir documentos) | ok | ok |

Nenhuma ciência foi dada, nenhum documento aberto, nenhum dispositivo liberado.
