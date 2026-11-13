# e-SAJ TJAM (acervo antigo) — validação de 13/11/2026

Portal `tjam-esaj`, host `consultasaj.tjam.jus.br`, login por desafio do A1 (mesmo
fluxo de TJSP/TJMS/TJAL). Cadastrado no registro; correção de `http://` herdada do
TJAL (`esaj.base.upgrade_scheme`, cherry-pick do commit 504a9d9).

Hérica, 2 logins por certificado (limite desta tarefa):

1. Login ok (CASTGC emitido; o redirect `http://consultasaj.tjam.jus.br/esajperfil/...`
   foi elevado a https no mesmo host). Leituras HTTP 200 autenticadas:
   intimações 0, audiências 0, petições 0, caixa postal 1 item.
   `esaj/api/auth/session` responde **404** no TJAM: `usuarioLogado` não pôde ser
   lido; a prova de sessão é o acesso às APIs `tarefas-adv` e à caixa postal sem
   voltar ao CAS. O conector `get_session`/`refresh` ainda falha no TJAM por isso.
2. O CAS devolveu, após a assinatura, `https://consultasaj.tjam.jus.br/tarefas-adv/erroDesconhecido.do;jsessionid=...`
   (página de erro do próprio portal). O guard recusou o endereço (`;` no caminho)
   e não houve sessão. Não repetido; sem terceira tentativa.

Pendente: achar o equivalente de `usuarioLogado` no TJAM e entender o
`erroDesconhecido.do` intermitente. Nenhum documento aberto, nenhuma ciência.
