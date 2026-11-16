# Gonçalves Guimarães — validação de login com A1 em 13/11/2026

Certificado A1 (e-CPF de José Gonçalves Guimarães Júnior, sócio, OAB/SC 35.675, validade 09/11/2027). O PIN abre o arquivo (conferido pelo `load_a1`). SHA-256 do `.pfx` antes: `26b0d821...8c3f75` (conferido de novo no fim).

Escopo: só login e página inicial, por HTTP. Nenhum `.env`, `portals.toml`, código ou `.pfx` foi alterado; `<PREFIX>_CERT=true` e demais variáveis só no ambiente do processo de teste. Sem 2FA ativado, sem troca de dados, sem ciência. Nenhum PIN, senha ou código é registrado aqui.

Sem QR do autenticador (pedido em 09/11, não chegou): onde o portal pede TOTP depois do certificado, o resultado é 🔑 "precisa do QR (TOTP)". Não é falha.

`portals.toml` do cliente está vazio; a lista de portais vem das intimações do DJEN (30 dias, OAB/SC 35.675) cruzadas com `src/jurisync/portals.py`. Foto do momento (13/11/2026).

Legenda: ✅ entrou (página autenticada); ❌ recusa do tribunal (mensagem exata); 🔑 precisa do QR (TOTP); ⛔ trava; "não testado" com a causa.

## Resultados por portal (atualizado a cada teste)

| Portal | Resultado | Método | Tentativas |
|---|---|---|---|

| trf1-2g | ❌ advogado sem cadastro nesse PJe. O SSO aceitou o A1 + TOTP (PDPJ) e o PJe redirecionou para `/pje/publico/usuario/cadastro.seam`, página "Cadastro de novo usuário · Justiça Federal da 1ª Região" (mensagem do próprio portal, sem texto de recusa). Não cadastrei nada | PJe `<PREFIX>_CERT=true` + TOTP PDPJ (QR 1, índice 7) | 3 (1 antes do QR + 2 com A1 + TOTP) |

| trf3-2g | ✅ A1 + TOTP (PDPJ) aceitos; página autenticada do PJe (conector `_authenticated`: host do portal, link de logout / Painel do advogado) | PJe certificate_totp, TOTP PDPJ (QR 1, índice 7) | 2 (1ª sem TOTP) |

| trf5-2g | ✅ A1 + TOTP (PDPJ) aceitos; página autenticada do PJe (host do portal, logout / Painel do advogado) | PJe certificate_totp, TOTP PDPJ (QR 1, índice 7) | 2 (1ª sem TOTP) |
| trf5-tru | ✅ A1 + TOTP (PDPJ) aceitos; página autenticada do PJe (host `pjett.trf5.jus.br`) | PJe certificate_totp, TOTP PDPJ (QR 1, índice 7) | 2 (1ª sem TOTP) |

| trt18-1g | ✅ A1 + TOTP (PDPJ) aceitos; página autenticada do PJe (`pje.trt18.jus.br/primeirograu/`) | PJe certificate_totp, TOTP PDPJ (QR 1, índice 7) | 2 (1ª sem TOTP) |
| trt18-2g | ✅ A1 + TOTP (PDPJ) aceitos; página autenticada do PJe (`pje.trt18.jus.br/segundograu/`) | PJe certificate_totp, TOTP PDPJ (QR 1, índice 7) | 2 (1ª sem TOTP) |
| tjrj-2g | ⛔ trava técnica, não é negativa: PJe 1.x (legado). O desafio `ASSINATURA_MENSAGEM` foi assinado (MD5withRSA, como o applet declara) e enviado a `logarPJeOffice.seam`; resposta: `Não foi possível realizar a autenticação: Verifique se o cartão ou token estão c` (texto truncado pelo próprio portal; não diz certificado recusado ou não cadastrado). Repetido com a cadeia ICP-Brasil completa (AC Certisign RFB G5, AC RFB, Raiz Brasileira, buscada no AIA público), mesmo resultado. O botão oficial exige PJeOffice em `localhost:8800` ou applet Java. Falta: PJeOffice real ou navegador com o A1 para confirmar | PJe legado, assinatura PJeOffice | 3 (não retestado em 13/11 a pedido: só registro) |
| tjsc-1g | ✅ A1 + TOTP aceitos; Painel do Advogado (`frmPainelAdvogado`, perfil `SC035675 - ADVOGADO`, usuário José Gonçalves Guimarães Júnior, `PHPSESSID`). Sem tela de seleção de perfil | A1 mTLS + TOTP (QR do José, perfil Eproc/TJSC) | 2 (a 1ª, sem TOTP, parou no formulário de código) |
| tjsc-2g | ✅ A1 + TOTP aceitos; Painel do Advogado (`frmPainelAdvogado`, `SC035675 - ADVOGADO`), sem seleção de perfil | A1 mTLS + TOTP | 2 (1ª sem TOTP) |
| trf4-1g-sc | ✅ A1 + TOTP aceitos; tela "Seleção de perfil" (`frmEscolherUsuario`: ADVOGADO e ADVOGADO-TITULAR); escolhido ADVOGADO (sem "definir como padrão") e abriu o Painel do Advogado (`frmPainelAdvogado`) | A1 mTLS + TOTP + seleção de perfil | 2 (1ª sem TOTP) |
| trf4-1g-rs | ✅ A1 + TOTP aceitos; "Seleção de perfil" (ADVOGADO / ADVOGADO-TITULAR), escolhido ADVOGADO; Painel do Advogado (`frmPainelAdvogado`) | A1 mTLS + TOTP + seleção de perfil | 2 (1ª sem TOTP) |
| trf4-1g-pr | ✅ A1 + TOTP aceitos; "Seleção de perfil" (ADVOGADO / ADVOGADO-TITULAR), escolhido ADVOGADO; Painel do Advogado (`frmPainelAdvogado`) | A1 mTLS + TOTP + seleção de perfil | 2 (1ª sem TOTP) |
| trf4-2g | ✅ A1 + TOTP aceitos; "Seleção de perfil" (ADVOGADO / ADVOGADO-TITULAR), escolhido ADVOGADO; Painel do Advogado (`frmPainelAdvogado`) | A1 mTLS + TOTP + seleção de perfil | 2 (1ª sem TOTP) |
| tjrs-1g | ✅ A1 + TOTP; Painel do Advogado (`frmPainelAdvogado`, `SC035675 - ADVOGADO`) | A1 mTLS + TOTP | 2 (1ª sem TOTP) |
| tjsp-1g | ✅ A1 + TOTP; Painel do Advogado (`frmPainelAdvogado`, `SC035675 - ADVOGADO`), sem tela de seleção de perfil | A1 mTLS + TOTP | 2 (1ª sem TOTP) |
| tjsp-2g | ✅ A1 + TOTP; Painel do Advogado (`frmPainelAdvogado`) | A1 mTLS + TOTP | 2 (1ª sem TOTP) |
| tjrj-eproc-1g | ✅ A1 + TOTP; Painel do Advogado (`frmPainelAdvogado`) | A1 mTLS + TOTP | 2 (1ª sem TOTP) |
| tjpr-1g | ✅ A1 + TOTP; Painel do Advogado (`frmPainelAdvogado`). Obs.: o SSO do TJPR vai direto do certificado ao formulário de código, sem a tela "Continue" (`kc-x509-login-info`); o conector antigo lê isso como `eproc did not accept the A1 certificate`, mas o tribunal aceitou o A1 | A1 mTLS + TOTP | 3 (2 sondagens sem TOTP) |
| trf1-1g | ✅ A1 + TOTP; página autenticada do PJe (link de logout + formulário JSF / "Painel do advogado" no host do portal) | PJe certificate_totp | 2 (1ª sem TOTP) |
| trf3-1g | ✅ A1 + TOTP; página autenticada do PJe (link de logout + formulário JSF / "Painel do advogado" no host do portal) | PJe certificate_totp | 2 (1ª sem TOTP) |
| trf5-1g | ✅ A1 + TOTP; página autenticada do PJe (link de logout + formulário JSF / "Painel do advogado" no host do portal) | PJe certificate_totp | 2 (1ª sem TOTP) |
| tjrj-1g | ✅ A1 + TOTP; página autenticada do PJe (link de logout + formulário JSF / "Painel do advogado" no host do portal) | PJe certificate_totp | 2 (1ª sem TOTP) |
| trf2-1g-rj | ✅ A1 aceito + TOTP na tela `2fa_codigo`; entrou logado (usuário José, perfil `SC035675 - ADVOGADO`) e o eproc abriu `pessoa_alterar&avisoCadastrarEmailSenha=true` ("Alterar Cadastro", aviso para cadastrar e-mail/senha). Não alterei nada | A1 mTLS (`login_cert`) + TOTP no formulário local | 2 (1ª sondagem sem TOTP) |
| trf2-1g-es | ✅ A1 aceito + TOTP na tela `2fa_codigo`; entrou logado (José, `SC035675 - ADVOGADO`) em `pessoa_alterar&avisoCadastrarEmailSenha=true` ("Alterar Cadastro"). Não alterei nada | A1 mTLS + TOTP no formulário local | 2 (1ª sondagem sem TOTP) |
| trf2-2g | ✅ A1 aceito + TOTP na tela `2fa_codigo`; entrou logado (José, `SC035675 - ADVOGADO`) em `pessoa_alterar&avisoCadastrarEmailSenha=true` ("Alterar Cadastro"). Não alterei nada | A1 mTLS + TOTP no formulário local | 2 (1ª sondagem sem TOTP) |
| tjmg-1g | ✅ A1 aceito + TOTP na tela `2fa_codigo` (login local, `login_cert` → `entrar_cert`); Painel do Advogado (`frmPainelAdvogado`, `SC035675 - ADVOGADO`, `MG`) | A1 mTLS + TOTP no formulário local (HTTP) | 1 |
| trf6-1g | ✅ Painel do Advogado (`controlador.php`, título "Painel do Advogado", perfil `SC035675 - ADVOGADO`) com navegador headless (Chrome) passando o WAF; botão "Certificado Digital" (`kc-login-certificate`) do SSO, A1 apresentado por mitmproxy local temporário, TOTP Eproc/TRF6 (QR 1, índice 0); "liberar dispositivo" (`saveDevice`) não marcado | navegador + A1 (mitmproxy) + TOTP | 1 |
| trf6-2g | ✅ Painel do Advogado (`eproc2g.trf6.jus.br`, título "Painel do Advogado", `SC035675 - ADVOGADO`); mesmo método do 1G, sem CAPTCHA | navegador + A1 (mitmproxy) + TOTP Eproc/TRF6 | 1 |

Atualização (13/11, tarde): PJe 2G testados com A1 + TOTP PDPJ (QR 1, índice 7) e TRF6 por navegador headless + mitmproxy local com o A1 + TOTP Eproc/TRF6 (índice 0). `_CERT`, `_2FA_DATA` e `_OTP_PROFILE_INDEX` só no ambiente do processo. "Liberar dispositivo" nunca marcado. Temporários, PEM extraído e `downloaded_files/` apagados. SHA-256 do `.pfx` igual antes e depois (`26b0d821...8c3f75`).
