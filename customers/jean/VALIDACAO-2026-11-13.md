# Jean — validação de login em 13/11/2026

Testes ao vivo de autenticação e leitura da página interna. Credenciais exclusivas desta cliente/conta, selecionadas por `JURISYNC_CUSTOMER`.

| Portal | Resultado | Evidência ou mensagem literal | Envios senha / TOTP |
|---|---|---|---|
| `trf1-1g` | ✅ Entrou | HTTP 200 no tribunal; logout e ViewState; menus: Processos, Expedientes, Acervo, Painel do advogado. | 1 / 1 |
| `trf1-2g` | ✅ Entrou | HTTP 200 no tribunal; logout e ViewState; menus: Processos | 1 / 1 |
| `tjap-1g` | ✅ Entrou | HTTP 200 no tribunal; logout e ViewState; menus: Processos, Expedientes, Acervo, Painel do advogado | 1 / 1 |
| `tjrs-1g` | ✅ Entrou | HTTP 200 no tribunal; painel do advogado e formulário frmPainelAdvogado; senha e TOTP aceitos | 1 / 1 |

## Escopo e limites

- Todos os acessos listados neste relatório chegaram à página interna ou painel autenticado, com senha e código TOTP aceitos.
- Uma tentativa de login por portal, sem reenvio de senha ou TOTP. Nenhum CAPTCHA, código por e-mail ou navegador visível foi necessário nos fluxos testados.
- PJe: rotina `login_session` do conector; HTTP 200 no host do tribunal, link de logout e ViewState confirmados.
- Eproc: formulários SSO reproduzidos por HTTP; retorno ao host do tribunal e formulário `frmPainelAdvogado` confirmados. A rota `browser_totp` do conector não foi executada.
- Validação limitada ao login e à página interna/painel. Não abri documentos, processos específicos ou intimações e não tomei ciência.
- Nenhuma configuração, senha, cadastro ou código do projeto foi alterado. `.env` e `portals.toml` conferidos por hash no processo de teste; cookies somente em memória.
- Relatório atualizado após cada portal. Script temporário removido ao concluir os dois clientes.

O resultado de `trf1-1g` foi preservado da execução anterior; esse portal não foi testado novamente na retomada. Os demais três portais foram consultados somente após conferir que ainda estavam pendentes no relatório.
