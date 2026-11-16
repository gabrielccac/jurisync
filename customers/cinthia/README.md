# Cinthia

Office of Cinthia Cardoso (João Paulo's credentials are in `customers/joao`). Scope from the proposal: PJe TRF1, TRF3,
TRF5, TJPE, TJBA; eproc TRF6 and TJMG; e-SAJ TJSP; ADVBOX.

`portals.toml` lists, per portal, the login method and the environment variables it needs (no values). The values go
in the gitignored `customers/cinthia/.env`. Check a setup without printing secrets:

```bash
JURISYNC_CUSTOMER=cinthia uv run python -m jurisync.customers cinthia
```
