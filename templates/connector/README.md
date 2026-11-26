# Molde de conector

Conector mínimo e executável que segue [docs/padroes.md](../../docs/padroes.md): login por usuário e senha num portal
fictício (`exemplo`), duas leituras e o erro, a annotation e a resposta no formato da plataforma. Os testes em
`tests/core/test_connector_template.py` rodam contra ele com uma sessão falsa, então o molde não envelhece em
silêncio: se uma regra do guia mudar, o teste quebra aqui primeiro.

| Arquivo | Papel |
|---|---|
| `client.py` | HTTP e login. Toda requisição passa por `request` (host permitido, timeout, sessão expirada). Sem MCP. |
| `parsers.py` | Funções puras: resposta do portal para `dict` da plataforma. Testadas com fixtures fictícias. |
| `server.py` | MCP: `create_server()`, as tools, `configured_portals()`, o `SessionPool` e `main()`. |

Para criar um conector: copie esta pasta para `src/jurisync/connectors/<portal>/`, troque `exemplo` pelo nome,
registre os portais em `portals.py` e o conector em `registry.py`, e copie o teste para
`tests/connectors/<portal>/`.
