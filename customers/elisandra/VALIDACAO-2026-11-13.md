# Elisandra — validação de login em 13/11/2026

| Portal | Resultado | Evidência ou mensagem literal | Envios senha / TOTP |
|---|---|---|---|
| `trf4-1g-sc` | ✅ Entrou | HTTP 200 no tribunal; painel do advogado e formulário frmPainelAdvogado; senha e TOTP aceitos | 1 / 1 |
| `trf4-1g-rs` | ✅ Entrou | HTTP 200 no tribunal; painel do advogado e formulário frmPainelAdvogado; senha e TOTP aceitos | 1 / 1 |
| `tjrs-1g` | ✅ Entrou | HTTP 200 no tribunal; painel do advogado e formulário frmPainelAdvogado; senha e TOTP aceitos | 1 / 1 |

## Escopo e limites

- Todos os acessos listados neste relatório chegaram à página interna ou painel autenticado, com senha e código TOTP aceitos.
- Uma tentativa de login por portal, sem reenvio de senha ou TOTP. Nenhum CAPTCHA, código por e-mail ou navegador visível foi necessário nos fluxos testados.
- Eproc: formulários SSO reproduzidos por HTTP; retorno ao host do tribunal e formulário `frmPainelAdvogado` confirmados. A rota `browser_totp` do conector não foi executada.
- Validação limitada ao login e à página interna/painel. Não abri documentos, processos específicos ou intimações e não tomei ciência.
- Nenhuma configuração, senha, cadastro ou código do projeto foi alterado. `.env` e `portals.toml` conferidos por hash no processo de teste; cookies somente em memória.
- Relatório atualizado após cada portal. Script temporário removido ao concluir os dois clientes.

Antes de cada acesso, o relatório foi consultado para evitar repetição de testes já registrados.
