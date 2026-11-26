# Private runtime data

Git-ignored except this file. Ignoring is not encryption: keep this folder on an
encrypted disk and out of shared folders.

- `customers/<name>/` — workflow state (for example Jean's publication pipeline)
- `advbox-*.json` — ADVBOX session cookie cache
- `eproc-smoke/` — raw pages saved by `cli/eproc_portal_smoke.py` when run
- `pje1g_watch_state.json` — Victor's TRF1 watcher baseline (its PJe session is the connector's, under `~/.cache/jurisync/pje/`)

Juridiq and PJe session caches live outside the repository, under `~/.cache/jurisync/`.
