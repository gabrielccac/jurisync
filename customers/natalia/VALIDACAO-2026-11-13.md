# Natália (Rasi Advocacia) — validação de login em 13/11/2026

Testes ao vivo somente de autenticação e leitura da página inicial, com as credenciais de `customers/natalia/.env` (`JURISYNC_CUSTOMER=natalia`). O login (CPF e senha) e os QRs são da conta de **Ana Flávia Pacheco Ramos**, sócia da Natália na Rasi Advocacia. O escritório passou esse login único para todos os portais e o uso foi autorizado pelo Gabriel. Nenhuma senha, código, QR ou CPF é reproduzido aqui.

| Portal | Resultado | Evidência | Tentativas |
|---|---|---|---|
| `trf6-1g` | ❌ | `eproc rejected the username/password (page: Nome de usuário ou senha inválida.)` | 1 |
| `trf6-2g` | ❌ | `eproc rejected the username/password (page: Nome de usuário ou senha inválida.)` | 1 |
| `tjrj-eproc-1g` | ❌ | `eproc rejected the username/password (page: Nome de usuário ou senha inválida.)` | 1 |
| `trf2-1g-rj` | ❌ | CAPTCHA "Enviar" clicado pelo conector, depois voltou ao login: `eproc rejected the username/password` (a página não trouxe texto de erro legível nas 2 tentativas; mesmo comportamento de ES e 2G) | 2 |
| `trf2-1g-es` | ❌ | CAPTCHA "Enviar" clicado pelo conector, depois `eproc rejected the username/password` (tela do eproc: `Senha ou usuário Inválidos`) | 2 |
| `trf2-2g` | ❌ | CAPTCHA "Enviar" clicado pelo conector, depois `eproc rejected the username/password` (tela do eproc: `Senha ou usuário Inválidos`) | 2 |
| `tjsp-1g` | ❌ | `eproc rejected the username/password (page: Nome de usuário ou senha inválida.)` | 1 |
| `tjsp-2g` | ❌ | `eproc rejected the username/password (page: Nome de usuário ou senha inválida.)` | 1 |
| `tjmg-1g` | ⛔ | `eproc asks for an image CAPTCHA (code typed from a picture); it cannot be solved automatically` (apareceu nas 2 tentativas) | 2 |
| `trf1-1g` | ✅ | HTTP 200 em `pje1g.trf1.jus.br`, "Quadro de avisos · Justiça Federal da 1ª Região", link `logout`, `javax.faces.ViewState` | 2 |
| `trf3-1g` | ✅ | HTTP 200 em `pje1g.trf3.jus.br`, "Quadro de avisos · Processo Judicial Eletrônico - TRF3 - 1º Grau", link `logout`, `ViewState` | 2 |
| `trf3-2g` | ✅ | HTTP 200 em `pje2g.trf3.jus.br`, "Quadro de avisos · Processo Judicial Eletrônico - TRF3 - 2º Grau", link `logout`, `ViewState` | 2 |

## PJe (TRF1, TRF3 1G e 2G): perfil de OTP errado no .env

- Na 1ª tentativa de cada um, a senha foi aceita (o SSO chegou ao formulário de OTP), mas o OTP foi recusado: `OTP submission did not redirect; the code may be invalid or expired`. O relógio da máquina está sincronizado.
- O `OTP_PROFILE_MATCH` configurado seleciona o perfil PDPJ cujo nome é o CPF (11 dígitos; é o 8º item da exportação). A exportação tem dois perfis PDPJ: o de nome "PJE" (índice 0) e esse do CPF.
- Na 2ª tentativa, só em memória (`OTP_PROFILE_MATCH` vazio e `OTP_PROFILE_INDEX=0` na variável de ambiente do processo), o perfil "PDPJ/PJE" foi aceito nos três portais.
- **Ação pendente (não gravei nada):** nas três chaves `PJE_1G`, `PJE_TRF3_1G` e `PJE_TRF3_2G`, trocar `*_OTP_PROFILE_MATCH` para escolher o perfil PDPJ "PJE" (por exemplo `*_OTP_PROFILE_INDEX=0` e `MATCH` vazio). O comentário do `portals.toml` sobre o perfil `11840189606` fica para outros tribunais.
- A tela de entrada é o "Quadro de avisos"; o texto "Processos" não apareceu nela, então a navegação interna não foi conferida.

## Eproc

- **TRF6 1G/2G, TJRJ, TJSP 1G/2G:** a credencial foi rejeitada logo no passo de usuário e senha, antes do OTP. A mesma senha foi aceita no SSO do PJe, então é provável que esses eprocs tenham senha própria ou exijam o usuário em outro formato (o `.env` guarda o CPF com pontuação, 14 caracteres). Não fiz 2ª tentativa para não arriscar bloqueio da conta com a mesma credencial. Para validar, a Ana Flávia precisa confirmar usuário e senha de cada eproc.
- **TRF2 1G RJ/ES e 2G:** o login pediu CAPTCHA ("Enviar"). O conector clicaria sozinho; neutralizei isso no teste para respeitar a regra e parei. Nada foi concluído, e o OTP não foi enviado.
- **TJMG 1G:** CAPTCHA de imagem (código digitado a partir de uma figura). Parei.

## Limites e alterações

- Nenhum portal passou de 2 tentativas. Só autenticação e leitura da página inicial, em modo headless; nenhum navegador visível.
- Nenhuma ciência, troca de senha ou alteração de cadastro. Nenhuma alteração em `.env`, `portals.toml` ou código; a troca de perfil de OTP foi só por variável de ambiente do processo.
- O cache de sessão do PJe foi desviado para uma pasta temporária, depois removida. Scripts e arquivos temporários foram apagados.
