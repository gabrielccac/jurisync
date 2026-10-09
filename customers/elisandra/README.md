# Elisandra

Eproc access for JFSC (`trf4-1g-sc`) and JFRS (`trf4-1g-rs`) on her credentials.

Portal access is in `portals.toml` (no values). Values are in the gitignored
`customers/elisandra/.env`, copied from `.env.example`. Select her with
`JURISYNC_CUSTOMER=elisandra`. Check the setup without printing secrets:

```bash
JURISYNC_CUSTOMER=elisandra uv run python -m jurisync.customers elisandra
```

DJEN: `djen-searches.json` holds her search payload (OAB 61216/RS, nomeAdvogado
"ELISANDRA BECKER", tribunal STJ, meio D); pass it to `djen_search_communications` with a
date window. A 03/09 to 03/10/2026 run returned 4 STJ communications for one REsp.

Planned: Tramitação Inteligente (native MCP) and a customer skill for the DJEN intimações
(her own payload), with manual task creation via chat. DJEN is still down, so that
part is not wired yet.
