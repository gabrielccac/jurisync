# TJSE — Portal da Advocacia e Defensoria Pública

Inspeção pública em 13/11/2026; zero tentativas de autenticação.

- Entrada: <https://www.tjse.jus.br/portaldoadvogado/>.
- Destino autenticado indicado pelo JavaScript público:
  <https://www.tjse.jus.br/tjnet/portaladv/index.wsp>.
- O formulário público pede **CPF, senha e CAPTCHA de imagem**.
- A imagem vem de `/tjnet/Captcha.jpg`.
- O JavaScript envia o login para `/tjnet/portaladv/loginjsoncpf.wsp`.
- Há uma janela de validação em duas etapas e chamadas para
  `/autenticacao/rest/auth/generate2f` e `/autenticacao/rest/auth/validateToken`.
  Essa etapa não foi alcançada; não foi atribuído a ela nenhum QR da cliente.
- Nenhuma opção de login A1 foi encontrada no formulário observado.

**Resultado: precisa de uma pessoa.** O CAPTCHA já aparece antes da autenticação.
Nenhum CAPTCHA foi resolvido, nenhuma senha enviada e nenhum processo/documento
lido. A existência da janela de segundo fator no HTML inicial não comprova senha
aceita ou envio de código.

O sistema não foi registrado como conector operacional. Não foi inventado um
fluxo de leitura pós-login nem criado um adaptador que pareça validado. Antes de
implementar autenticação e leituras reais, precisa-se de uma sessão autorizada
após a etapa humana e da senha do Portal do Advogado, caso não esteja disponível.

Fontes primárias: [área do advogado do TJSE](https://www.tjse.jus.br/portal/advogado),
[entrada e JavaScript público do portal](https://www.tjse.jus.br/portaldoadvogado/).
