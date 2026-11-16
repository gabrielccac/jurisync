# Validação Tucujuris (TJAP), 16/11/2026

Branch `feat/tucujuris`, cliente `breno`, portal `tjap-tucujuris`. Mapeamento do portal no `docs/connectors/tucujuris.md`.

## Resumo

| Etapa | Resultado |
|---|---|
| Exploração anônima (página pública, login, bundles JS) | OK: SPA Knockout com API JSON em `/api/`, atrás da Cloudflare (o curl_cffi `chrome` passa) |
| Login (1 tentativa) | **Recusado**: `ERRO` "Essa conta de usuário não existe" |
| 2FA, processos, documentos, comunicações | Não executados (o login não passou) |
| Testes offline | 28 testes do conector, com fixtures sintéticas; suíte completa verde (1179 passed) |

## Login

- Uma tentativa: `POST /api/publico/login` com `TJAP_TUCUJURIS_USERNAME`, que tem um CPF de 11 dígitos.
- O portal respondeu HTTP 200 com `status: ERRO`, mensagem "Essa conta de usuário não existe". A conta não foi
  encontrada, então a senha nem chegou a ser avaliada, e não há risco de bloqueio.
- Causa: a página de login diz que o **advogado** entra com o número da OAB seguido da seccional (ex.: `9999AP`).
  O CPF só vale para procuradores, defensores, peritos e "demais usuários". Isso estava no `login.html`; o título do
  campo ("Matricula, CPF, OAB ou E-mail") não deixa isso claro.
- Paramos aqui, como mandam as regras de lockout. Não houve segundo login: corrigir o usuário exige um valor que não
  temos (a OAB do Breno), e não editamos `.env` de cliente.

## Para revalidar

1. No `customers/breno/.env`, colocar em `TJAP_TUCUJURIS_USERNAME` a OAB com a seccional (ex.: `1234AP`; o conector
   aceita `OAB/AP 1234`).
2. Fazer uma rodada com um login: `tucujuris_list_processes`, `tucujuris_list_intimacoes` (`naolidas`,
   `ultimos10dias`), `tucujuris_get_process` de um processo da lista, e `tucujuris_read_documents` de um arquivo sem
   `pendingCiencia`.
3. Conferir os formatos reais contra as fixtures (`tests/connectors/tucujuris/fixtures/`) e o download pelo header
   `Auth-Token`. Se tudo passar, incluir `tucujuris` em `hosted/app.py:TENANT_READY`.

## Ciência

Nenhuma comunicação foi listada nem aberta. A rota que registra ciência (`processar-comunicacao`, "Marcar como lido")
é recusada pelo `client.guard`, com testes.
