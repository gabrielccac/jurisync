# Wesley

Portal and tool access for Wesley (`wmsadvocacia`).

## Portal access

`portals.toml` lists the court portals and the environment variables each login
needs (no values). The values go in the gitignored `customers/wesley/.env`,
copied from `.env.example`. Select him with `JURISYNC_CUSTOMER=wesley`.

```bash
JURISYNC_CUSTOMER=wesley uv run python -m jurisync.customers wesley
```

| Portal | Key | Method | Credential | Notes |
| --- | --- | --- | --- | --- |
| PJe TRF1 1º grau | `trf1-1g` | `certificate_totp` (`PJE_1G_CERT=true`) | A1 e-CPF (no TOTP asked) | validated 2026-10-19 |
| PJe TRF1 2º grau | `trf1-2g` | `certificate_totp` (`PJE_2G_CERT=true`) | A1 e-CPF + authenticator | validated 2026-10-19 |
| PJe TRF3 1º grau | `trf3-1g` | `certificate_totp` (`PJE_TRF3_1G_CERT=true`) | A1 e-CPF + authenticator | validated 2026-10-19 (counters, inbox, Push, process, documents, expediente act) |
| Eproc TRF4 (2º grau) | `trf4-2g` | `certificate_totp` (`TRF4_2G_CERT=true`) | A1 e-CPF (mTLS) + `Eproc/TRF4` profile of `QR_Wesley_Eproc.jpeg` | login and open-deadlines queue validated 2026-10-19 |
| Eproc TRF4 SC (1º grau) | `trf4-1g-sc` | `certificate_totp` (`TRF4_1G_SC_CERT=true`) | A1 + `Eproc/TRF4` profile | login, deadlines queue, process and HTML document validated 2026-10-19 |
| Eproc TRF4 RS (1º grau) | `trf4-1g-rs` | `certificate_totp` (`TRF4_1G_RS_CERT=true`) | A1 + `Eproc/TRF4` profile | login and deadlines queue validated 2026-10-19; no RS process to read |
| Eproc TRF4 PR (1º grau) | `trf4-1g-pr` | `certificate_totp` (`TRF4_1G_PR_CERT=true`) | A1 + `Eproc/TRF4` profile | login and process read validated 2026-10-19 (portal wired in this change) |
| Eproc TRF6 1º/2º grau | `trf6-1g`, `trf6-2g` | `browser_totp` | user + password + `Eproc/TRF6` profile | not tested: needs `TRF6_USERNAME`/`TRF6_PASSWORD`; the tribunal disabled the certificate login |
| PJe TRF5 1º grau/JEF | `trf5-1g` | `certificate_totp` | A1 e-CPF + authenticator | validated 2026-10-19 |
| PJe TJAP 1º grau | `tjap-1g` | — | A1 e-CPF would be `TJAP_1G_CERT=true` | not declared in `portals.toml`: not credentialed, the SSO returns `ssoCadastroCallback.seam` |

The PDPJ password account is rejected on `trf1-2g`/`trf5-1g`, but the A1
certificate logs in on all three TRF portals. The authenticator is the `PDPJ`
profile from `QR_Wesley.jpeg` (migration URI), reused as `PJE_2G_2FA_DATA` and
`PJE_TRF5_1G_2FA_DATA`. TJAP still needs Wesley's credenciamento before the
certificate session is accepted.

## Juridiq

Not a court portal, so it is declared in the reserved `[env]` table of
`portals.toml` (variables that belong to no portal); the values live in `.env`:

```toml
[env]
secrets = ["JURIDIQ_EMAIL", "JURIDIQ_PASSWORD"]
```

Login validated 2026-10-15.

## Eproc and the A1 certificate

Eproc logs in over mutual TLS: the login page's certificate button moves to a sibling SSO host
(`eproc-sso-certificado` at TRF4, `certificado-sso-eproc` at TRF6) that asks for the client
certificate, then the SSO asks the authenticator code. The authenticator export
`QR_Wesley_Eproc.jpeg` holds the profiles `Eproc/TRF4`, `Eproc/TRF6` and `Eproc/TRF2`; each portal
picks its own with `<PREFIX>_OTP_PROFILE_MATCH`. TRF6 does not take part: its login page says "o acesso com certificado digital está instável. Favor
acessar com usuário, senha e 2FA", so it uses the password flow (browser login, which also clicks
through the bot-protection challenge).
