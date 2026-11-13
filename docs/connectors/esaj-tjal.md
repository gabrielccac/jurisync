# e-SAJ TJAL

Portal `tjal-esaj`: `https://www2.tjal.jus.br` (entrada em `/esaj/portal.do`), CNJ J=8 TR=02. Mesmo login por desafio
com o A1 do TJSP/TJMS (`sajcas/login`, formulário `formCertificado`), sem mTLS e sem autenticador.

Os aplicativos ficam na raiz do host (`/sajcas`, `/caixapostal`, `/tarefas-adv`, `/cpopg`), como no TJSP; por isso
`root="/"`.

## Peculiaridade

O CAS do TJAL (atrás de balanceador que termina o TLS) devolve o redirect do ticket como `http://www2.tjal.jus.br/...`.
O conector recusa qualquer URL que não seja https; agora `esaj.base.upgrade_scheme` sobe o esquema de `http` para
`https` apenas quando o host é o do próprio portal e a porta é a padrão. Outro host continua recusado.

## Validação ao vivo (13/11/2026, só leitura)

- Login com o A1: `CASTGC` emitido; `esaj/api/auth/session` com `usuarioLogado: true` (24 OABs, sem código por e-mail).
- Leituras: `esaj_get_session`, `intimacoes` (19 itens), `audiencias` (1), `peticoes` (2) e caixa postal (20 itens): ok.
- Leitura de processo (`load_process`, `cpopg`, documentos): **não validada ao vivo**. O limite de 3 tentativas de login
  foi consumido (1ª: trava do redirect http; 2ª: login; 3ª: leituras acima). O adaptador `tjal.py` usa `cpopg` como o
  TJSP; a consulta pública pode exigir CAPTCHA, que não é contornado.
- 2º grau (`cposg`) não está cadastrado.
