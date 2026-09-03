# Portfolio Tracker

Quarterly portfolio tracking dashboard for myFund/XTB CSV exports. Self-hosted
Flask + SQLite app, deployed as a systemd service on a Raspberry Pi behind
Tailscale, backed up daily to Google Drive.

## Features

- **Add Data tab** (`/import`) — the three steps for a new quarter in order: positions CSV,
  then cash/PPK/mortgage, then the contributions XLSX
- **CSV import** from myFund quarterly exports (auto-detects the snapshot date from the filename)
- **Cash-flow import** from the myfund.pl "Wkład i wartość" XLSX export (contributions & withdrawals; idempotent re-import)
- **Dashboard**: summary cards, timeline chart, Money In vs Value chart with lifetime returns, breakdown table, treemaps by tag and account
- **Compare view**: diff any two quarters side-by-side, with change-by-tag and net-worth-bridge charts
- **Forecast**: Monte Carlo net-worth projection with what-if sliders (horizon, market return, contribution rate)
- **Quarterly review**: optional LLM-written summary of the newest quarter (see below)
- **Retirement planner**: models Polish tax wrappers (IKE/IKZE/PPK), ZUS and the pre-60 bridge to project the earliest feasible retirement age
- **PPK tracking**: entered per quarter alongside cash and mortgage; counts inside the portfolio total and appears as its own tag/account
- **NBP currency rates** for non-PLN positions
- **Password-gated** when `DASHBOARD_PASSWORD` is set (disabled in dev mode)

## Project structure

| File / dir | Purpose |
|---|---|
| `app.py` | Flask routes, login gate, `create_app()` factory |
| `db.py` | SQLite schema + helpers (DB path from `DATABASE_PATH` env var) |
| `nbp.py` | NBP currency-rate fetcher |
| `gemini.py` | Gemini API client for the quarterly review (optional feature) |
| `retirement.py` | Retirement simulation engine (age gating, wrapper taxes, ZUS, PPK) |
| `import_data.py` | myFund CSV parser |
| `static/common.js` | Helpers shared by all pages (`formatPLN`, account badges, theme) |
| `static/app.js`, `static/compare.js`, `static/forecast.js` | Dashboard, Compare, and Forecast frontends |
| `static/retirement.js` | Retirement planner frontend |
| `static/import.js` | Add Data page — the only frontend that writes data |
| `templates/` | Jinja templates (`index`, `compare`, `forecast`, `login`) |
| `tests/` | pytest suite (parsers, cash-flow aggregation, API, auth) |
| `requirements.txt` | Runtime deps, pinned: flask, requests, gunicorn, openpyxl |
| `requirements-dev.txt` | Runtime deps + pytest (local only, never installed on the Pi) |
| `migrate_fix_xtb_ticker.py` | One-off DB migration |

## Local development

```bash
cd <repo-path>
source venv/bin/activate
python3 app.py                  # Flask dev server on http://127.0.0.1:5001 (no auth)
```

Run prod-like with gunicorn + auth:

```bash
DATABASE_PATH=./portfolio.db DASHBOARD_PASSWORD=test SECRET_KEY=dev-key \
  venv/bin/python -m gunicorn 'app:create_app()' --bind 127.0.0.1:5001
```

## Tests

Install the dev dependencies once:

```bash
venv/bin/python -m pip install -r requirements-dev.txt
```

Then run the suite:

```bash
venv/bin/python -m pytest
```

Every test runs against a temporary SQLite file — `portfolio.db` is never
touched. Coverage focuses on the logic where a silent error would corrupt data
without any visible symptom: CSV/XLSX parsing, the cash-flow-to-quarter
bucketing rules, the lifetime-returns calculation, and the auth gate.

## Production (Raspberry Pi)

Runs as a systemd service on the Pi, reachable via Tailscale at `http://<your-pi-hostname>:5001`.

| Component | Path |
|---|---|
| Code | `/opt/portfolio/app/` (clone of this repo, owned by `portfolio` user) |
| Database | `/var/lib/portfolio/portfolio.db` (`portfolio:portfolio`, mode 640) |
| Secrets | `/etc/portfolio.env` (`root:portfolio`, mode 640) |
| Systemd unit | `/etc/systemd/system/portfolio.service` |
| Backup script | `/opt/portfolio/backup.sh` (root-owned, mode 755) |
| Local backups | `/var/lib/portfolio/backups/` (7-day rotation) |
| Remote backups | `gdrive:portfolio-backups/` (90-day rotation via rclone) |
| Backup log | `/var/log/portfolio-backup.log` |
| Cron schedule | `5 4 * * * /opt/portfolio/backup.sh` (root crontab) |

### Environment variables (`/etc/portfolio.env`)

| Variable | Purpose |
|---|---|
| `PORT` | Port gunicorn binds to (5001 in prod) |
| `SECRET_KEY` | Signs Flask session cookies (32-byte hex) |
| `DASHBOARD_PASSWORD` | Login password — auth is disabled if unset |
| `DATABASE_PATH` | SQLite file location |
| `GEMINI_API_KEY` | Enables the quarterly review — the feature is hidden if unset |
| `GEMINI_MODEL` | Gemini model id (optional; defaults to a Flash model) |

## Quarterly review (optional)

Set `GEMINI_API_KEY` to a key from Google AI Studio and a card appears on the
dashboard that writes a short prose review of the newest quarter. Leave it unset
and the feature stays completely hidden — nothing is sent anywhere.

**What gets sent.** Only derived figures: percentage changes, allocation
percentages and percentage-point deltas, contribution pace relative to the
recent average, and position names with the account suffix stripped.
**Absolute amounts and account names are never included.** This matters because
Google's free tier permits training use and human review; the paid tier has
stronger terms. The restriction is enforced by tests in
`tests/test_commentary.py`, not just by convention.

Generation is explicit — it happens when you press the button, never on page
load. Results are cached per quarter, so a normal visit makes no API call. If
the underlying figures change afterwards the card says so and offers a
regenerate.

The prompt instructs the model to use only the supplied figures, calculate
nothing, and give no investment advice or predictions.

To inspect exactly what would be sent before enabling it:

```bash
venv/bin/python -c "import json, app; print(json.dumps(app._build_commentary_payload(app._build_dashboard_data()), indent=2, ensure_ascii=False))"
```

## Operational commands (run on the Pi)

### Service

```bash
sudo systemctl status portfolio          # current state
sudo systemctl restart portfolio         # restart (e.g. after a code update)
sudo systemctl stop portfolio            # stop
sudo systemctl start portfolio           # start
sudo journalctl -u portfolio -f          # tail live application logs
sudo journalctl -u portfolio -n 100      # last 100 log lines
```

### Deploy a code update

After pushing changes from the laptop to GitHub:

```bash
sudo -u portfolio -H bash -c '
  cd /opt/portfolio/app
  git pull
  source venv/bin/activate
  pip install -r requirements.txt
'
sudo systemctl restart portfolio
```

### Backups

```bash
sudo /opt/portfolio/backup.sh                                  # run on demand
sudo tail -50 /var/log/portfolio-backup.log                    # backup log
ls -la /var/lib/portfolio/backups/                             # list local snapshots
sudo rclone ls gdrive:portfolio-backups \
    --config /root/.config/rclone/rclone.conf                  # list remote snapshots
```

### Restore from backup

```bash
sudo systemctl stop portfolio
sudo cp /var/lib/portfolio/backups/portfolio-YYYY-MM-DD.db \
        /var/lib/portfolio/portfolio.db
sudo chown portfolio:portfolio /var/lib/portfolio/portfolio.db
sudo systemctl start portfolio
```

To restore from a remote snapshot instead, first pull it down:

```bash
sudo rclone copy gdrive:portfolio-backups/portfolio-YYYY-MM-DD.db /tmp/ \
    --config /root/.config/rclone/rclone.conf
# then follow the steps above with /tmp/portfolio-YYYY-MM-DD.db as the source
```

### Edit secrets

```bash
sudo nano /etc/portfolio.env
sudo systemctl restart portfolio    # reload env
```

### Edit the systemd unit

```bash
sudo nano /etc/systemd/system/portfolio.service
sudo systemctl daemon-reload
sudo systemctl restart portfolio
```

## Network access

UFW allows traffic only on the `tailscale0` interface plus SSH. The dashboard is
reachable from any device logged into the same tailnet at
`http://<your-pi-hostname>:5001`. The port is **not** exposed to the public internet.
