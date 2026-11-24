# Deploying the hosted server

One VPS serves every office at `https://<host>/tribunais/mcp` (every court system and DJEN) plus
`https://<host>/advbox/mcp` and `https://<host>/juridiq/mcp` for the offices that use them; `https://<host>/mcp`
(everything at one URL) is deprecated and kept for existing setups (see `docs/hosted.md` for the design and
`docs/roadmap.md` for the order of the offices). Everything here is a script; the only manual
inputs are the VPS address, the domain (or none yet) and the CAPTCHA key.

| File | Runs on | Does |
|---|---|---|
| `push.sh` | our machine | `git archive HEAD` → VPS, then `install.sh` there. Only committed files travel. |
| `install.sh` | VPS (root) | packages, Google Chrome, LibreOffice, user `jurisync`, code in `/opt/jurisync/app` (root's, read-only for the service), `uv sync --extra captcha-ocr --extra pdf-ocr` as root, the OCR models in `/opt/jurisync/models`, vault key, systemd units, proxy. Idempotent: run again to update. |
| `set-secret.sh` | VPS (root) | stores `JURISYNC_VAULT_KEY` / `JURISYNC_CAPTCHA_API_KEY` as systemd credentials (`systemd-creds` encrypted when available). |
| `jurisync-admin` | VPS (root) | `jurisync-hosted` as user `jurisync` with the service's settings and credentials (installed in `/usr/local/bin`). |
| `check-office.sh` | VPS (root) | the first login of each portal of one office, one at a time, stopping at the first failure. |
| `backup.sh` + `systemd/jurisync-backup.timer` | VPS | daily store + vault + profiles tarball in `/var/backups/jurisync` (14 days; optional rclone copy). |
| `quick-tunnel.sh` + `systemd/jurisync-quicktunnel.service` | VPS | temporary `https://<random>.trycloudflare.com` while there is no domain. |
| `caddy/Caddyfile`, `cloudflared/config.yml.example` | VPS | HTTPS with a domain: Caddy (ports 80/443) or a named Cloudflare Tunnel (no open port). |
| `smoke_test.py` | anywhere | OAuth metadata, 401, sign-in, tools per office, isolation. No portal login. |

Layout on the VPS: code `/opt/jurisync/app` (with `.venv`; `root:jurisync`, the service only reads it), uv and any
Python it downloads in `/opt/jurisync/bin` and `/opt/jurisync/python`, OCR models `/opt/jurisync/models` (root's,
read-only for the service), settings `/etc/jurisync/jurisync.env` (no
secrets), state `/var/lib/jurisync/hosted` (`store.sqlite` in WAL mode, `vault/`), connector sessions and downloads
`/var/lib/jurisync/data` and `/var/lib/jurisync/downloaded_files` (`JURISYNC_STATE_DIR`), customer profiles
`/var/lib/jurisync/customers`, credentials `/etc/credstore.encrypted/` (or `/etc/credstore/`), backups
`/var/backups/jurisync`. The service writes only under `/var/lib/jurisync`.

## 1. Server (once; again for each update)

Ubuntu 24.04 x86_64, 2 vCPU / 4 GB at least (each Eproc password login is a headless Chrome; `JURISYNC_MAX_LOGINS=2`),
Brazilian region if possible (some portals challenge foreign datacenter IPs). From our machine, on a commit:

```bash
deploy/push.sh ubuntu@<vps> --proxy quick                                  # no domain yet: trycloudflare address
deploy/push.sh ubuntu@<vps> --proxy caddy --domain mcp.<dominio>.com.br    # domain pointing at the VPS (A record)
deploy/push.sh ubuntu@<vps> --proxy tunnel --domain mcp.<dominio>.com.br   # domain on Cloudflare, then cloudflared/
```

Then on the VPS:

```bash
sudo /opt/jurisync/app/deploy/set-secret.sh JURISYNC_CAPTCHA_API_KEY   # OpenRouter key (TJES WAF, TJTO image); typed, not echoed
sudo /opt/jurisync/app/deploy/set-secret.sh --reveal JURISYNC_VAULT_KEY # once: keep it in the password manager
sudo systemctl restart jurisync-hosted
journalctl -u jurisync-quicktunnel -n 5                                  # the temporary address (quick tunnel only)
```

The vault key is generated on the server and never leaves it except through `--reveal`. Without that copy a backup
cannot be opened elsewhere. Moving from the quick tunnel to the domain is `push.sh ... --proxy caddy --domain ...`:
the base URL changes, so each office adds the connector again.

### OCR of scanned PDFs

On by default. `install.sh` installs the `pdf-ocr` extra and fetches pdf-inspector's models once into
`/opt/jurisync/models` (`python -m jurisync.core.ocr`, checked by an offline read); the unit points
`PDF_INSPECTOR_MODEL_CACHE` there, and a document read never downloads. A scanned document is read up to
`JURISYNC_OCR_MAX_PAGES` pages (30) and `JURISYNC_OCR_SECONDS` (90). Kill switch: `JURISYNC_OCR=off` in
`/etc/jurisync/jurisync.env` and `systemctl restart jurisync-hosted`; scanned documents then come back as
`ocr_needed`, as they do when the models are missing (install.sh warns and goes on).

## 2. Offices

On our machine (the folder with `customers/<name>/.env`, A1 files and QR images), for the offices of the wave:

```bash
uv run jurisync-hosted provision export --out ~/rollout --passphrase-file ~/.config/jurisync/rollout.passphrase \
  --customers-dir ~/orca/jurisync/customers --customer victor fernanda herica
```

It prints, per office, the portals going to the server, the connectors, and what is left out and why (A3 token,
missing secrets, portals not served). An office whose `customers/<name>/settings.toml` has `[hosted] mode = "local"`
(barbara, cinthia: A3 on their own computers) gets no bundle, connectors or users; `apply` only records it, and
`check-office.sh` has nothing to test for it. `~/rollout/plan.json` has no values: add each office's lawyers to its `users`
list, then export again (users are kept). The passphrase file is created once (0600) and is never printed.

```bash
scp -r ~/rollout <vps>:/tmp/rollout
ssh <vps> 'sudo install -m 600 -o jurisync /dev/stdin /var/lib/jurisync/rollout.passphrase' < ~/.config/jurisync/rollout.passphrase
ssh <vps> 'sudo rsync -a --chown=jurisync:jurisync /tmp/rollout/ /var/lib/jurisync/rollout/ && rm -rf /tmp/rollout'
ssh -t <vps> sudo jurisync-admin provision apply /var/lib/jurisync/rollout \
  --passphrase-file /var/lib/jurisync/rollout.passphrase --office victor fernanda herica
ssh <vps> 'sudo shred -u /var/lib/jurisync/rollout.passphrase'
```

`apply` creates or updates each tenant (connectors from the plan), installs its profile, stores the bundle in the
vault without logging in, and creates the users with random passwords written to
`/var/lib/jurisync/hosted/new-user-passwords.txt` (0600; hand each one over through another channel, then delete the
file). Then, one office at a time, the first real login of each portal, from the VPS:

```bash
sudo /opt/jurisync/app/deploy/check-office.sh victor     # one login per portal, 30 s apart, stops at the first failure
```

## 3. Check

```bash
/opt/jurisync/app/.venv/bin/python /opt/jurisync/app/deploy/smoke_test.py https://<host>          # public checks
/opt/jurisync/app/.venv/bin/python /opt/jurisync/app/deploy/smoke_test.py https://<host> \
  --plan /var/lib/jurisync/rollout/plan.json --user advogada@victor.com.br=/root/victor.password --call-saved-searches
sudo jurisync-admin calls victor --limit 20
```

The smoke test signs in on `/tribunais/mcp` (only the office's court systems, no `advbox_*`/`juridiq_*` there), on
`/advbox/mcp` and `/juridiq/mcp` for an office that has them (that system's tools only) and on the deprecated `/mcp`.

In Claude (Settings → Connectors → Add custom connector) or ChatGPT (developer mode connector), add "Jurisync
Tribunais" at `https://<host>/tribunais/mcp` and, for an office with ADVBOX or Juridiq, "Jurisync ADVBOX" at
`https://<host>/advbox/mcp` / "Jurisync Juridiq" at `https://<host>/juridiq/mcp` (the office's `endpoints` in
`plan.json`); the sign-in page asks for the user's e-mail and password, once per connector. After Claude and ChatGPT have both
signed in once, set `JURISYNC_REQUIRE_TOKEN_RESOURCE=1` in `/etc/jurisync/jurisync.env` and restart.

Office plugins (skills) for Claude Code / Cowork pointing at the server:

```bash
uv run jurisync-hosted provision plugin plugins/victor --url https://<host> --out dist/victor-remote
```

The copy has one server `tribunais` (`/tribunais/mcp`) for the plugin's court systems and DJEN and one per management
system it uses (`advbox` → `/advbox/mcp`).

Skills that use filing or PJe draft writes (`victor-pje-filing`) are flagged: those stay on the local install.

## Restore

```bash
sudo systemctl stop jurisync-hosted
sudo tar -xzf /var/backups/jurisync/jurisync-<stamp>.tar.gz -C /tmp
sudo rm -f /var/lib/jurisync/hosted/store.sqlite-wal /var/lib/jurisync/hosted/store.sqlite-shm  # the old WAL is not the backup's
sudo rsync -a /tmp/jurisync-<stamp>/store.sqlite /tmp/jurisync-<stamp>/vault /var/lib/jurisync/hosted/
sudo rsync -a /tmp/jurisync-<stamp>/customers/ /var/lib/jurisync/customers/
sudo chown -R jurisync:jurisync /var/lib/jurisync && sudo systemctl start jurisync-hosted
```

On a new VPS, after `install.sh` (which generates a fresh key when none exists), put the saved key back before
restoring: `sudo deploy/set-secret.sh JURISYNC_VAULT_KEY` asks for it without echo (never pass it on a command line).
