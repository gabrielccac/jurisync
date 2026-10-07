# Private runtime data

Git-ignored except this file. Ignoring is not encryption: keep this folder on an
encrypted disk and out of shared folders.

- `customers/<name>/` — workflow state (for example Jean's publication pipeline)
- `advbox-*.json` — ADVBOX session cookie cache
- `inss-browser-profile/` — default dedicated Chrome profile for INSS GERID
- `eproc-smoke/` — raw pages saved by `cli/eproc_portal_smoke.py` when run
- `pje1g_watch_*.json` — Victor's TRF1 watcher baseline and cookie cache

Juridiq and PJe session caches live outside the repository, under `~/.cache/jurisync/`.
