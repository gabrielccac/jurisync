# e-SAJ TJAM — inspeção com A1 em 13/11/2026

Portal legado opcional: <https://consultasaj.tjam.jus.br/esaj/portal.do>.
Foi usado o fluxo nativo do conector e-SAJ, com um `Portal` somente no processo
do teste e o A1 da Hérica. Não houve cadastro permanente nem alteração de conta.

Uma tentativa de certificado. O CAS retornou HTTP 302 após a assinatura e
encaminhou para `/esajperfil/abrirConfirmacaoDados.do`. Após o retorno do ticket
CAS para `/esajperfil/j_spring_cas_security_check`, houve um redirecionamento HTTP.
O guard de transporte recusou esse endereço antes de requisitá-lo:

```
Unexpected e-SAJ login redirect URL: http://consultasaj.tjam.jus.br/esajperfil/abrirConfirmacaoDados.do
```

**Resultado: bloqueio técnico de transporte, sem sessão autenticada validada.**
O avanço do CAS não é prova de painel disponível. Não se seguiu o endereço HTTP,
não se tentou contornar a proteção, não se confirmou cadastro e não se realizou
segunda tentativa. Não houve leitura de processo ou documento.

O portal não foi cadastrado como operacional. O arquivo do A1, `.env` e
`portals.toml` permaneceram idênticos. Nenhum cookie, PIN ou certificado foi
registrado no relatório.
