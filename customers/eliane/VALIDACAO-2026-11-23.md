
## Portais nunca validados — rodada de 23/11/2026 (somente leitura)

Código: worktree `chore/hardening` (0e5f779), camada MCP. Nada foi enviado aos tribunais: o conector parou antes do login.

| Portal | Resultado | Motivo |
|---|---|---|
| trt5-2g | ⛔ `[login_missing]` | "Set PJE_A1_PIN to the A1 certificate password" |
| trt6-2g | ⛔ `[login_missing]` | idem |
| trt7-1g | ⛔ `[login_missing]` | idem |
| trt7-2g | ⛔ `[login_missing]` | idem |
| trt24-1g | ⛔ `[login_missing]` | idem |
| trt24-2g | ⛔ `[login_missing]` | idem |

Causa real: há usuário e senha de cada TRT no `.env`, mas nenhum `TRT*_2FA_DATA` (nem de nenhum outro portal; o README já registrava "falta o QR do autenticador"). Sem autenticador o login por senha não é considerado pronto e o conector cai no caminho do certificado, por isso a mensagem fala do A1 (a Eliane não tem A1 configurado). A mensagem engana: o que falta é o autenticador.

O `portals.toml` da Eliane declara só `trt7-1g`/`trt7-2g` (como `password_totp`); faltam os blocos `trt5-2g`, `trt6-2g`, `trt24-1g` e `trt24-2g` (`TRT*_USERNAME`, `TRT*_PASSWORD`, `TRT*_2FA_DATA`). O comentário "TRTs ainda não existem em portals.py" está desatualizado: TRT2/3/4/5/6/7/9/24 já estão registrados.

O que pedir à Eliane: o QR do autenticador PDPJ/PJe (PJe-JT) da conta dela (`TRT*_2FA_DATA` / `_2FA_QR`), ou o certificado A1 (.pfx + PIN).
