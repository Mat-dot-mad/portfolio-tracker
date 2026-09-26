# Portfolio Tracker

Quarterly portfolio tracking dashboard for myFund/XTB CSV exports. Self-hosted
Flask + SQLite app, deployed as a systemd service on a Raspberry Pi behind
Tailscale, backed up daily to Google Drive.

## Features

- **Add Data tab** (`/import`) — the three steps for a new quarter in order: positions CSV,
  then cash/PPK/mortgage, then the contributions XLSX
- **Reviewed CSV imports** from myFund quarterly exports: validate rows, preview totals and holding changes, then save or atomically replace a snapshot while preserving manual balances.
- **Cash-flow import** from the myfund.pl "Wkład i wartość" XLSX export: preview date coverage, totals and added/removed events before replacing the full history.
- **Dashboard**: summary cards, timeline chart, Money In vs Value with dated cash-flow XIRR, breakdown table, treemaps by tag and account
- **Data completeness**: snapshot and contribution dates, missing quarterly snapshots, unrecorded versus confirmed-zero manual balances, and explicit contribution-history coverage
- **Compare view**: diff any two quarters side-by-side, with change-by-tag and net-worth-bridge charts
- **Forecast**: Monte Carlo net-worth projection with what-if sliders (horizon, market return, contribution rate)
- **Quarterly review**: optional LLM-written summary of the newest quarter (see below)
- **Retirement planner**: models Polish tax wrappers (IKE/IKZE/PPK), ZUS and the pre-60 bridge. Temporary edits do not overwrite the baseline; save named scenarios, compare them with the baseline, or explicitly update the baseline.
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
| `performance.py` | Shared historical investment returns and dated cash-flow XIRR |
| `data_quality.py` | Snapshot gaps, balance completeness and coverage indicators |
| `import_workflow.py` | Strict upload validation, signed previews and atomic saves |
| `retirement.py` | Retirement simulation engine (age gating, wrapper taxes, ZUS, PPK) |
| `import_data.py` | myFund CSV parser |
| `static/common.js` | Helpers shared by all pages (`formatPLN`, account badges, theme) |
| `static/app.js`, `static/compare.js`, `static/forecast.js` | Dashboard, Compare, and Forecast frontends |
| `static/retirement.js` | Retirement planner frontend |
| `static/import.js` | Add Data page — imports and manual balances |
| `templates/` | Jinja templates (`index`, `compare`, `forecast`, `login`) |
| `tests/` | pytest suite (parsers, cash-flow aggregation, API, auth) |
| `requirements.txt` | Runtime deps, pinned: flask, requests, gunicorn, openpyxl |
| `requirements-dev.txt` | Runtime deps + pytest (local only, never installed on the Pi) |
| `migrate_fix_xtb_ticker.py` | One-off DB migration |

## Accounting and scenario behavior

Historical period returns are computed once on the server from investments plus
cash, excluding PPK and mortgage debt. Net contributions are subtracted from the
change in that tracked capital; contributions are approximated as arriving at
period end. These are approximate period returns, not XIRR. The forecast,
retirement planner and quarterly commentary share these inputs. Money In vs Value
uses the same tracked capital; the net-worth chart still includes PPK and debt.
No additional PPK contribution data is required. With no cash-flow import,
contributions are unknown and historical return estimates are incomplete.

The forecast applies those historical investment returns to net worth as a
simplified projection. It does not add future PPK payroll contributions separately;
the retirement planner models those from its salary settings.

Retirement edits only calculate a temporary draft. **Save scenario** saves the
selected named scenario (or creates one when Baseline is selected); **Save as new**
creates a separate scenario. **Update baseline** explicitly saves the current
inputs as the baseline. **Discard changes** reloads the selected saved plan.
Scenarios retain complete assumptions but recalculate from the latest portfolio
balances, showing the snapshot date used. Simulation paths use independent seeded
random streams so early failures do not change subsequent paths between scenarios.
Resetting the baseline preserves named scenarios.

Import previews expire after one hour. Saving revalidates both the file and the
reviewed stored positions/cash flows; changes require a new preview. Invalid dates,
unreadable or non-finite amounts block the import. Ignored rows appear in the
preview. Snapshot replacement keeps the snapshot ID and manual balances.

The scenario table is created automatically by `create_app()` on startup. This is
an additive schema update; no manual migration or additional dependencies are
needed. Existing retirement settings become the baseline unchanged. Deploy via
the normal pull-and-restart procedure below.

## Data completeness and personal returns

Blank manual balances mean **not recorded**; enter `0` to confirm there is no
cash, PPK or mortgage balance. Existing absent entries remain unknown after this
update: previous versions discarded entered zeros, so the migration cannot
reliably recover that intent. Existing nonzero balances remain recorded. A saved
zero PPK balance also overrides any older fallback balance in the retirement planner.

The dashboard, Forecast, Retirement and Add Data pages show holdings dates,
contribution event dates and a collapsible history of missing balances and
quarterly gaps. Totals still use recorded inputs, with missing amounts treated as
zero and marked as incomplete. Older missing balances do not block lifetime XIRR
when the final cash balance and full contribution history are known.

The last cash-flow event does **not** prove the export covers later quiet periods.
After reviewing the full XLSX history, use **Add Data → Confirm contribution
coverage** to explicitly confirm all deposits and withdrawals from the beginning
through a chosen date. Every successful XLSX replacement clears that confirmation;
failed imports and holdings replacements preserve it. Confirmation is tied to the
reviewed cash-flow revision to avoid approving an export changed in another tab.

The Money In vs Value card replaces its old simple annualization with **XIRR**, a
personal money-weighted annual return based on actual dates and a 365-day year
([Microsoft's XIRR definition](https://support.microsoft.com/en-us/Excel/functions/xirr-function)).
Deposits are negative, withdrawals positive, and investments plus cash at the
latest snapshot form the final positive value. Same-day flows are netted and
post-snapshot events are excluded. PPK and mortgage debt are excluded throughout.
This differs from the approximate quarterly investment returns used by forecasts.

XIRR requires a recorded final cash balance (including explicit zero) and full
history confirmed through the snapshot date. Otherwise the card explains the
missing input. The solver searches annual rates above -100% (to within 1e-12)
through 100,000,000%, partitions the NPV curve at derivative roots, and withholds a
single return if it finds multiple solutions, no solution, or cannot resolve a
complex pattern safely. XIRR is annualized even for periods shorter than a year.

The new `cash_flow_coverage` table is created automatically on startup. Existing
holdings, balances, cash flows, scenarios and reviews are not rewritten.

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
node --test tests/js/*.test.cjs  # browser calculation regressions (local Node.js)
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
| `GEMINI_MODEL` | Gemini model id (optional; defaults to `gemini-3.8-flash`) |

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
regenerate. Changing `GEMINI_MODEL` also marks cached reviews as stale; it
does not send data or regenerate anything automatically.

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

### Import history and undo

The Add Data page records each successful CSV/XLSX upload with its filename,
UTC timestamp, totals, changes and validation warnings. History starts when this
feature is deployed; earlier uploads cannot be reconstructed. The first new
upload still saves the existing data as its undo destination. Original uploaded
files are not retained; their SHA-256 hashes and parsed before/after data are
stored in SQLite and included in normal database backups.

Choose **Preview undo**, review the current and restored totals, then
**Confirm undo**. Undo applies to one snapshot's positions or the full
contribution history. Later imports for that same dataset must be undone first.
Position replacement preserves manual balances. Undoing the creation of a new
quarter removes that quarter and its generated review, but is blocked if manual
balances have since been saved (including confirmed zeros); replace its CSV to
correct positions instead. Cash-flow undo restores the previous coverage
confirmation as well as the previous events.

The preview expires after one hour and is checked again under a database write
lock. Changed data requires a new preview or prevents undo. Imports, journal
records, restores and undo timestamps commit atomically. Undone records remain
visible; this is an audit trail, not a redo feature. A new `import_history` table
and index are created automatically on startup; no existing financial data is
migrated or changed.

### Retirement shortfall explanations

The retirement page reports how many of the 300 seeded simulations first run
short with locked capital still present, and how many fail after exhausting
capital. A single actual failing run, selected at the middle first-shortfall
age, shows spending, income, net withdrawals, withdrawal tax and the unfunded
gap. Remaining locked balances include their configured access ages. The
headline, first-failure statistics and chart use the same 300 return sequences.
The year-by-year table remains a separate run chosen by median ending capital.

Balances are recorded after that year's growth, income and spending. Accessible
net balances estimate remaining cash after withdrawal taxes. Chart capital is
before tax, and locked-capital medians are calculated directly rather than by
subtracting medians. Failed runs continue for illustration; accumulated
unfunded spending is not treated as debt or recovered from later balances.
Fixed-return mode produces identical paths and is labelled accordingly.

The planner uses investment balances, not net worth. Mortgage debt is not
subtracted and repayment schedules are not simulated. Include expected
retirement repayments in annual spending; spending remains constant in today's
money and does not automatically fall at a mortgage payoff date.
