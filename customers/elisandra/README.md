# Elisandra

Eproc access for JFSC (`trf4-1g-sc`) and JFRS (`trf4-1g-rs`) on her credentials.

Portal access is in `portals.toml` (no values). Values are in the gitignored
`customers/elisandra/.env`, copied from `.env.example`. Select her with
`JURISYNC_CUSTOMER=elisandra`. Check the setup without printing secrets:

```bash
JURISYNC_CUSTOMER=elisandra uv run python -m jurisync.customers elisandra
```

DJEN: `settings.toml` `[[djen.searches]]` holds her saved search (`stj-diario`: OAB 61216/RS, tribunal STJ);
`djen_search_saved` runs it for a date window (pass `meio='D'` for the Diário only). A 03/09 to 03/10/2026 run returned 4 STJ communications for one REsp.

Planned: Tramitação Inteligente (native MCP) and a customer skill for the DJEN intimações
(her own payload), with manual task creation via chat. DJEN is still down, so that
part is not wired yet.
