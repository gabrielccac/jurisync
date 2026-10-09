# Cinthia

Test customer for wiring new court portals (TRF6 PJe, TRF3 PJe, eproc). Her credentials
are used to validate each login flow before it goes to other customers.

## Portal access

`portals.toml` lists, per portal, the login method and the environment variables it
needs (no values). The values go in the gitignored `customers/cinthia/.env`, copied from
`.env.example`. Servers pick the customer with `JURISYNC_CUSTOMER=cinthia`. Check a setup
without printing secrets:

```bash
JURISYNC_CUSTOMER=cinthia uv run python -m jurisync.customers cinthia
```

Add a portal here only after its row exists in `src/jurisync/portals.py`.
