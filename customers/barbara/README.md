# Bárbara Sisti

Portais do PDF "INFORMAÇÕES PARA AUTOMAÇÃO ESCRITÓRIO" (27/10). Certificado A3 em nuvem da Certisign (desktopID /
RemoteID) no computador do escritório + TOTP de cada portal (exportação do autenticador, 2 QRs).

## Login com o A3 em nuvem

- O app Certisign desktopID precisa estar instalado e aberto. O `portals.toml` fixa `PJE_A3_MODULE = "certisign"` e os
  portais usam `method = "a3_totp"`; o `.env` só leva os `*_2FA_DATA` (nenhum PIN).
- Na primeira assinatura do processo abre a janela do desktopID (PIN + e-token). Essa autorização vale para todos os
  logins do mesmo processo (`PJE_A3_SESSION_IDLE` = 20 min ociosa, `PJE_A3_SESSION_TTL` = 120 min).
- `jurisync-smoke barbara` faz todos os portais com uma janela só.

## Validação ao vivo (27/11, uma autorização para a rodada inteira)

Login completo (certificado, TOTP e sessão): TRF1 1G/2G, TRF3 1G/2G, TRF5 1G/2G e TJMT 1G (PJe, conta PDPJ
"TRF 1 - 3 e 5" do autenticador), TJRJ Eproc 1G, TJSC 1G/2G, TRF4 2G, TRF6 1G/2G (Eproc, conta Eproc/<tribunal>).

Pendentes:

- TJSP 1G/2G: o certificado é aceito, mas o TOTP da primeira conta Eproc/TJSP foi recusado. O export tem duas contas
  Eproc/TJSP com o mesmo nome; a segunda ainda não foi testada.
- TJMG 1G e TRF2 2G: Eproc clássico, sem Keycloak X.509. O certificado entra depois de usuário e senha do Eproc
  (`TJMG_1G_USERNAME/PASSWORD`, `TRF2_2G_USERNAME/PASSWORD`), que ainda não temos. O TRF2 também oferece o autenticador
  Certisign (`autenticador.certisign.com.br`), que o conector ainda não implementa.

Fora do registro: TJAC, TJMS, TJRO, 2º grau de TJMG/TJRJ/TJMT, SIREA, Projudi TJPR. TRF2/TRF4 1º grau: o PDF só traz o
link genérico (seção RJ/ES e SC/RS/PR não informada). O export também tem perfis GERID e SIREA.
