# João Paulo

João Paulo (office of Cinthia; previdenciário). Earlier test customer for wiring new court portals. His credentials
are used to validate each login flow before it goes to other customers.

## Portal access

`portals.toml` lists, per portal, the login method and the environment variables it
needs (no values). The values go in the gitignored `customers/joao/.env`, copied from
`.env.example`. Servers pick the customer with `JURISYNC_CUSTOMER=joao`. Check a setup
without printing secrets:

```bash
JURISYNC_CUSTOMER=joao uv run python -m jurisync.customers joao
```

Add a portal here only after its row exists in `src/jurisync/portals.py`.
