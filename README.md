# Portfolio Tracker

Quarterly portfolio tracking dashboard for myFund/XTB CSV exports. Self-hosted
Flask + SQLite app, deployed as a systemd service on a Raspberry Pi behind
Tailscale, backed up daily to Google Drive.

## Features

- **CSV import** from myFund quarterly exports (auto-detects the snapshot date from the filename)
- **Dashboard**: summary cards, timeline chart, breakdown table, treemaps by tag and account
- **Compare view**: diff any two quarters side-by-side
- **Static HTML export**: a single self-contained HTML file with Dashboard + Compare bundled — works offline, viewable on any device
- **NBP currency rates** for non-PLN positions
- **Password-gated** when `DASHBOARD_PASSWORD` is set (disabled in dev mode)

## Project structure

| File / dir | Purpose |
|---|---|
| `app.py` | Flask routes, login gate, `create_app()` factory |
| `db.py` | SQLite schema + helpers (DB path from `DATABASE_PATH` env var) |
| `nbp.py` | NBP currency-rate fetcher |
| `import_data.py` | myFund CSV parser |
| `static/app.js`, `static/compare.js` | Dashboard + Compare frontend |
| `templates/` | Jinja templates (`index`, `compare`, `login`, `export`) |
| `requirements.txt` | flask, requests, gunicorn |
| `migrate_fix_xtb_ticker.py` | One-off DB migration |

## Local development

```bash
cd /Users/mateusz/Projects/portfolio
source venv/bin/activate
python3 app.py                  # Flask dev server on http://127.0.0.1:5001 (no auth)
```

Test prod-like with gunicorn + auth:

```bash
DATABASE_PATH=./portfolio.db DASHBOARD_PASSWORD=test SECRET_KEY=dev-key \
  venv/bin/python -m gunicorn 'app:create_app()' --bind 127.0.0.1:5001
```

## Production (Raspberry Pi)

Runs as a systemd service on `mat-pi`, reachable via Tailscale at `http://mat-pi:5001`.

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
`http://mat-pi:5001`. The port is **not** exposed to the public internet.
