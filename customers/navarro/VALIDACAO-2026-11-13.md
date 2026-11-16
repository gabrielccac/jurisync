# Grupo Navarro — validação de login em 13/11/2026

Teste ao vivo por HTTP, usando o login de senha + TOTP do conector PJe e as credenciais exclusivas de `customers/navarro/.env`. Nenhum segredo é reproduzido neste relatório.

| Portal | Resultado | Evidência | Tentativas |
|---|---|---|---|
| PJe TRF1 1º grau (`trf1-1g`) | ✅ Entrou | Senha e TOTP aceitos; HTTP 200 no tribunal, link de logout, ViewState e navegação interna com Processos, Expedientes e Acervo. | 1 |
| PJe TRF1 2º grau (`trf1-2g`) | ❌ Falhou | Mensagem literal do portal: “Usuário ou senha inválido”. Não chegou ao formulário de TOTP. | 1 |
| PJe TJPA 1º grau (`tjpa-1g`) | ✅ Entrou | Senha e TOTP aceitos; HTTP 200 no tribunal, link de logout, ViewState e navegação com Painel do advogado, Processos, Expedientes e Acervo. | 1 |

## Escopo e limites

- O perfil configurado `PDPJ` foi selecionado sem ambiguidade nos três portais. Nos dois sucessos, o TOTP foi aceito; no TRF1 2º grau ele não foi solicitado.
- O TRF1 2º grau recebeu uma única tentativa; não repeti a mesma credencial rejeitada nem alterei seu formato. Não é possível concluir pela mensagem se o problema é a credencial ou a habilitação da conta nesse portal.
- Não houve CAPTCHA ou necessidade de navegador visível.
- Nenhuma senha, cadastro ou configuração foi alterada; nenhuma ciência de intimação foi tomada. Nenhum contato com o cliente.
- GERID fora do teste. Não confirmei se a conta pertence a Giovanny ou Bettania.
- Validação restrita à autenticação e leitura da página interna. Não testei leitura de documentos, processos específicos ou intimações.
- Somente este relatório foi gravado permanentemente. Script temporário e resultado sanitizado em `/tmp` removidos após a consolidação; cookies autenticados mantidos apenas em memória e descartados ao fechar as sessões.
