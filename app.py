import hashlib
import hmac
import json
import os
import random
import re

from flask import Flask, render_template, request, redirect, url_for, jsonify, session

import db
import gemini
import nbp
import import_data
import retirement

app = Flask(__name__)
# SECRET_KEY signs the session cookie so the browser cannot forge "authenticated".
# In production it comes from /etc/portfolio.env (32-byte hex from secrets.token_hex).
# A fixed dev fallback is fine because dev mode usually has DASHBOARD_PASSWORD unset
# (auth disabled), so cookie integrity doesn't matter there.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-for-production")

# Reject oversized uploads before buffering them. Real imports are tiny (the
# CSV and XLSX exports are tens of KB), so 10 MB is generous headroom while
# still stopping a mis-picked file from being read into memory.
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

# Session cookie hardening. SameSite=Lax is the modern browser default, but
# setting it explicitly means we don't depend on that default holding.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


@app.errorhandler(413)
def payload_too_large(_e):
    """Return JSON for oversized uploads.

    Both import endpoints are called via fetch() and parse the response as
    JSON, so Flask's default HTML error page would surface as an unhelpful
    JSON parse error in the browser instead of the real reason.
    """
    limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({"error": f"File too large (limit: {limit_mb} MB)."}), 413


# --- Authentication -------------------------------------------------------
#
# Pattern mirrors the watchlist app: a single shared password lives in the
# DASHBOARD_PASSWORD env var. If it's unset (dev mode) the whole site is open.
# If it's set, every request is intercepted by `require_login` and redirected
# to /login until the user submits the right password.

@app.before_request
def require_login():
    password = os.environ.get("DASHBOARD_PASSWORD")
    if not password:
        return  # auth disabled — dev mode
    # Allow the login page itself and the static asset directory through.
    if request.endpoint in ("login", "logout", "static"):
        return
    if not session.get("authenticated"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        expected = os.environ.get("DASHBOARD_PASSWORD", "")
        submitted = request.form.get("password", "")
        # compare_digest takes constant time regardless of where the strings
        # diverge, so response timing can't be used to guess the password
        # character by character. Encode first: it rejects non-ASCII str
        # arguments, and the password may contain Polish characters.
        if expected and hmac.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8")):
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Wrong password"), 401
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))


# --- Routes ---------------------------------------------------------------

@app.route("/")
def dashboard():
    return render_template("index.html")


def _build_dashboard_data():
    """Builds the full dashboard data payload for /api/dashboard."""
    snapshots = db.get_snapshots()  # Ordered by snapshot_date DESC
    timeline = db.get_all_snapshots_summary()

    # Load positions for ALL snapshots (ordered ASC for column display)
    snapshots_asc = list(reversed(snapshots))
    all_positions = {}
    for s in snapshots_asc:
        all_positions[s["id"]] = db.get_positions(s["id"])

    # Latest snapshot details
    latest = snapshots[0] if snapshots else None
    latest_positions = all_positions.get(latest["id"], []) if latest else []
    latest_manual = db.get_manual_entries(latest["id"]) if latest else []

    # Aggregate latest positions by tags (for doughnut charts)
    by_tags = {}
    for p in latest_positions:
        tag = p["tags"] or "Other"
        by_tags[tag] = by_tags.get(tag, 0) + p["value_pln"]

    # Aggregate latest positions by account (for doughnut charts)
    by_account = {}
    for p in latest_positions:
        account = p["account"] or "Other"
        by_account[account] = by_account.get(account, 0) + p["value_pln"]

    # Totals from latest
    portfolio_total = sum(p["value_pln"] for p in latest_positions)
    cash_total = sum(e["amount_pln"] for e in latest_manual if e["type"] == "cash")
    mortgage_total = sum(e["amount_pln"] for e in latest_manual if e["type"] == "mortgage")

    # Annotate the timeline with cash-flow info if any cash flows have been imported.
    # Pre-snapshot history is rolled into the first snapshot (per design: lump it
    # at the earliest snapshot since we have no portfolio value before that).
    cf_summary = db.get_cash_flow_summary()
    if timeline and cf_summary["count"] > 0:
        period_starts = [t["snapshot_date"] for t in timeline]
        net_per_period = db.get_net_contributions_by_period(period_starts)
        cumulative = 0.0
        for i, t in enumerate(timeline):
            t["net_contributions"] = net_per_period[i]
            cumulative += net_per_period[i]
            t["cumulative_invested"] = cumulative
    else:
        # No cash flows imported yet — emit zeros so the frontend can branch on this
        for t in timeline:
            t["net_contributions"] = 0
            t["cumulative_invested"] = 0

    # Lifetime aggregates: invested vs. current wealth.
    # Market gains = (current portfolio + current cash) − net invested.
    # Mortgage isn't part of "what we put in" — it's a separate liability.
    #
    # IMPORTANT: current_wealth is as of the LAST SNAPSHOT, so the invested
    # figure must be capped at the same date. The raw cf_summary net_invested
    # runs through the latest XLSX row, which can be weeks past the snapshot —
    # using it would count post-snapshot deposits as "money in" before they
    # show up as value, understating market gains. timeline[-1]'s
    # cumulative_invested is already capped at the snapshot date.
    current_wealth = portfolio_total + cash_total
    invested_at_snapshot = (
        timeline[-1]["cumulative_invested"] if timeline else cf_summary["net_invested"]
    )
    lifetime = {
        "available": cf_summary["count"] > 0,
        "deposited": cf_summary["deposited"],
        "withdrawn": cf_summary["withdrawn"],
        "net_invested": invested_at_snapshot,
        "current_wealth": current_wealth,
        "market_gains": current_wealth - invested_at_snapshot,
        "earliest_date": cf_summary["earliest_date"],
        # End of the measured period = the snapshot the wealth figure comes from
        # (keeps the annualized-return calculation consistent).
        "latest_date": latest["snapshot_date"] if latest else cf_summary["latest_date"],
    }

    return {
        "latest": latest,
        "portfolio_total": portfolio_total,
        "cash_total": cash_total,
        "mortgage_total": mortgage_total,
        "net_worth": portfolio_total + cash_total - mortgage_total,
        "by_tags": by_tags,
        "by_account": by_account,
        "all_positions": all_positions,
        "quarters": [{"id": s["id"], "quarter": s["quarter"]} for s in snapshots_asc],
        "timeline": timeline,
        "snapshots": snapshots,
        "manual_entries": latest_manual,
        "lifetime": lifetime,
    }


@app.route("/api/dashboard")
def api_dashboard():
    """Returns all data needed by the single-page dashboard."""
    return jsonify(_build_dashboard_data())


# --- Quarterly commentary -------------------------------------------------
#
# The only feature that sends portfolio data off this machine. The payload is
# deliberately restricted to percentages, deltas and position names: no
# absolute amounts and no account names. tests/test_commentary.py enforces
# that, because Google's free tier permits training use and human review.

def _strip_account(name, account):
    """Remove the account suffix myFund appends to position names.

    'iShares Core MSCI World UCITS ETF (Acc) (IWDA.AS) (BOSSA IKZE)'
      -> 'iShares Core MSCI World UCITS ETF (Acc) (IWDA.AS)'

    Position names are allowed in the payload but account names are not, and
    myFund bakes the latter into the former.
    """
    if not name:
        return name
    cleaned = name
    if account:
        cleaned = cleaned.replace(f"({account})", "")
    # Any remaining trailing group that names a broker/wrapper we know about.
    cleaned = re.sub(
        r"\s*\((?:BOSSA[^()]*|XTB(?:\s*\([^()]*\))?|ING|Interactive Brokers|"
        r"tastytrade|Obligacje Skarbowe|IKE Obligacje)\)\s*$",
        "",
        cleaned,
    )
    return cleaned.strip()


def _pct_change(current, previous):
    """Percent change, or None when the base is zero/missing."""
    if not previous:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def _build_commentary_payload(data):
    """Derive the figures sent to the LLM from the dashboard payload.

    Pure and unit-testable. Emits ONLY relative measures plus position names.
    Deliberately contains no PLN amounts and no account names.
    """
    timeline = data["timeline"]
    if len(timeline) < 2:
        return None

    curr, prev = timeline[-1], timeline[-2]
    curr_nw = curr["portfolio_total"] + curr["cash_total"] - curr["mortgage_total"]
    prev_nw = prev["portfolio_total"] + prev["cash_total"] - prev["mortgage_total"]
    contributions = curr.get("net_contributions") or 0

    # Market-only return: strip contributions out of the net-worth move, the
    # same definition the forecast page uses.
    market_return_pct = None
    if prev_nw:
        market_return_pct = round((curr_nw - prev_nw - contributions) / abs(prev_nw) * 100, 1)

    # Contribution pace vs. the previous four quarters (excluding this one and
    # the first snapshot, whose figure includes lumped pre-snapshot history).
    recent = [t.get("net_contributions") or 0 for t in timeline[1:-1]][-4:]
    contribution_vs_recent_pct = None
    if recent:
        avg = sum(recent) / len(recent)
        if avg:
            contribution_vs_recent_pct = round((contributions - avg) / abs(avg) * 100, 0)

    # Allocation by tag, current vs. previous, in percentage points.
    def alloc(snapshot_id):
        positions = data["all_positions"].get(snapshot_id, [])
        total = sum(p["value_pln"] for p in positions)
        if not total:
            return {}
        by_tag = {}
        for p in positions:
            by_tag[p["tags"] or "Other"] = by_tag.get(p["tags"] or "Other", 0) + p["value_pln"]
        return {tag: round(v / total * 100, 1) for tag, v in by_tag.items()}

    curr_alloc, prev_alloc = alloc(curr["id"]), alloc(prev["id"])
    # sorted() matters: iterating the set directly gives an order that varies
    # between processes (string hash randomisation), which would change the
    # serialised JSON and make the cached commentary look permanently stale.
    alloc_change_pp = {
        tag: round(curr_alloc.get(tag, 0) - prev_alloc.get(tag, 0), 1)
        for tag in sorted(set(curr_alloc) | set(prev_alloc))
    }

    # Positions worth remarking on: material holdings (>=1% of the portfolio)
    # ranked by how much they moved.
    def by_key(snapshot_id):
        out = {}
        for p in data["all_positions"].get(snapshot_id, []):
            key = p["ticker"] or p["name"]
            entry = out.setdefault(key, {"value": 0.0, "name": p["name"], "account": p["account"]})
            entry["value"] += p["value_pln"]
        return out

    curr_pos, prev_pos = by_key(curr["id"]), by_key(prev["id"])
    curr_total = sum(e["value"] for e in curr_pos.values()) or 1
    movers = []
    for key, entry in curr_pos.items():
        weight_pct = round(entry["value"] / curr_total * 100, 1)
        if weight_pct < 1.0:
            continue
        before = prev_pos.get(key)
        movers.append({
            "name": _strip_account(entry["name"], entry["account"]),
            "weight_pct": weight_pct,
            # Change in the position's VALUE, which moves on purchases and sales
            # as well as price. Naming it plainly "change_pct" led the model to
            # report a position that was topped up as though the security itself
            # had appreciated.
            "value_change_pct": _pct_change(entry["value"], before["value"]) if before else None,
            "is_new": before is None,
        })
    movers.sort(key=lambda m: abs(m["value_change_pct"] or 0), reverse=True)

    return {
        "quarter": curr["quarter"],
        "previous_quarter": prev["quarter"],
        "portfolio_change_pct": _pct_change(curr["portfolio_total"], prev["portfolio_total"]),
        "net_worth_change_pct": _pct_change(curr_nw, prev_nw),
        "market_return_pct_excluding_contributions": market_return_pct,
        "contribution_vs_recent_4q_average_pct": contribution_vs_recent_pct,
        "allocation_pct_by_tag": curr_alloc,
        "allocation_change_pp_by_tag": alloc_change_pp,
        "notable_positions": movers[:8],
        "quarters_of_history": len(timeline),
        "notes": [
            "All monetary amounts withheld by design. Percentages only.",
            "notable_positions[].value_change_pct is the change in the position's "
            "VALUE. It reflects buying or selling as well as price movement, so it "
            "must NOT be described as the security appreciating or performing.",
            "Only portfolio-level market_return_pct_excluding_contributions has "
            "contributions removed. Position-level figures do not.",
        ],
    }


def _payload_hash(payload):
    """Stable fingerprint of the payload, used to detect stale commentary.

    sort_keys makes the hash independent of dict ordering, so it stays
    consistent across processes and Python runs.
    """
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _commentary_state():
    """Shared setup for both commentary endpoints.

    Returns (snapshot_id, payload, payload_json, payload_hash) or None when
    there isn't enough history to say anything.
    """
    data = _build_dashboard_data()
    payload = _build_commentary_payload(data)
    if payload is None:
        return None
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return data["timeline"][-1]["id"], payload, payload_json, _payload_hash(payload)


@app.route("/api/commentary", methods=["GET"])
def api_get_commentary():
    """Return the cached review. Never calls the API — generation is explicit."""
    if not gemini.is_configured():
        return jsonify({"available": False, "reason": "GEMINI_API_KEY not set"})

    state = _commentary_state()
    if state is None:
        return jsonify({"available": False, "reason": "Needs at least two quarters"})

    snapshot_id, _payload, _payload_json, payload_hash = state
    cached = db.get_commentary(snapshot_id)
    if not cached:
        return jsonify({"available": True, "text": None})

    return jsonify({
        "available": True,
        "text": cached["text"],
        "generated_at": cached["generated_at"],
        "model": cached["model"],
        # Underlying figures changed since generation (e.g. after a re-import).
        "stale": cached["payload_hash"] != payload_hash,
    })


@app.route("/api/commentary", methods=["POST"])
def api_generate_commentary():
    """Generate and cache the review. This is what sends data to Google."""
    if not gemini.is_configured():
        return jsonify({"error": "GEMINI_API_KEY is not set"}), 503

    state = _commentary_state()
    if state is None:
        return jsonify({"error": "Need at least two quarters of history"}), 400

    snapshot_id, _payload, payload_json, payload_hash = state
    try:
        text = gemini.generate_commentary(payload_json)
    except ValueError as e:
        return jsonify({"error": str(e)}), 502

    model = gemini.get_model()
    db.save_commentary(snapshot_id, text, model, payload_hash)
    return jsonify({
        "ok": True,
        "text": text,
        "model": model,
        "generated_at": db.get_commentary(snapshot_id)["generated_at"],
        "stale": False,
    })


@app.route("/compare")
def compare():
    return render_template("compare.html")


@app.route("/forecast")
def forecast():
    return render_template("forecast.html")


@app.route("/api/compare")
def api_compare():
    """Returns data for comparing two quarters."""
    id_a = request.args.get("a", type=int)
    id_b = request.args.get("b", type=int)

    snapshots = db.get_snapshots()  # DESC
    snapshots_asc = list(reversed(snapshots))

    if not id_a or not id_b:
        # Default to last two quarters
        if len(snapshots_asc) >= 2:
            id_a = snapshots_asc[-2]["id"]
            id_b = snapshots_asc[-1]["id"]
        elif len(snapshots_asc) == 1:
            id_a = id_b = snapshots_asc[0]["id"]
        else:
            return jsonify({"error": "No snapshots available"}), 404

    snap_a = db.get_snapshot(id_a)
    snap_b = db.get_snapshot(id_b)
    if not snap_a or not snap_b:
        return jsonify({"error": "Snapshot not found"}), 404

    positions_a = db.get_positions(id_a)
    positions_b = db.get_positions(id_b)
    manual_a = db.get_manual_entries(id_a)
    manual_b = db.get_manual_entries(id_b)

    # Totals
    portfolio_a = sum(p["value_pln"] for p in positions_a)
    portfolio_b = sum(p["value_pln"] for p in positions_b)
    cash_a = sum(e["amount_pln"] for e in manual_a if e["type"] == "cash")
    cash_b = sum(e["amount_pln"] for e in manual_b if e["type"] == "cash")
    mortgage_a = sum(e["amount_pln"] for e in manual_a if e["type"] == "mortgage")
    mortgage_b = sum(e["amount_pln"] for e in manual_b if e["type"] == "mortgage")

    return jsonify({
        "snapshot_a": snap_a,
        "snapshot_b": snap_b,
        "positions_a": positions_a,
        "positions_b": positions_b,
        "totals": {
            "portfolio": [portfolio_a, portfolio_b],
            "cash": [cash_a, cash_b],
            "mortgage": [mortgage_a, mortgage_b],
            "net_worth": [
                portfolio_a + cash_a - mortgage_a,
                portfolio_b + cash_b - mortgage_b,
            ],
        },
        "quarters": [{"id": s["id"], "quarter": s["quarter"], "snapshot_date": s["snapshot_date"]} for s in snapshots_asc],
    })


@app.route("/api/manual-entries/<int:snapshot_id>", methods=["GET"])
def api_get_manual_entries(snapshot_id):
    entries = db.get_manual_entries(snapshot_id)
    return jsonify(entries)


@app.route("/api/manual-entries/<int:snapshot_id>", methods=["POST"])
def api_save_manual_entries(snapshot_id):
    snapshot = db.get_snapshot(snapshot_id)
    if not snapshot:
        return jsonify({"error": "Snapshot not found"}), 404

    data = request.get_json()
    entries = data.get("entries", [])
    db.save_manual_entries(snapshot_id, entries)
    return jsonify({"ok": True})


@app.route("/api/nbp-rate/<currency>/<date>")
def api_nbp_rate(currency, date):
    """Fetch the NBP exchange rate for a currency on a given date."""
    try:
        result = nbp.get_rate(currency, date)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/snapshots/<int:snapshot_id>", methods=["DELETE"])
def api_delete_snapshot(snapshot_id):
    snapshot = db.get_snapshot(snapshot_id)
    if not snapshot:
        return jsonify({"error": "Snapshot not found"}), 404
    db.delete_snapshot(snapshot_id)
    return jsonify({"ok": True})


@app.route("/api/import-csv", methods=["POST"])
def api_import_csv():
    """Import a myFund CSV export via file upload."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename or not f.filename.endswith(".csv"):
        return jsonify({"error": "Please upload a .csv file"}), 400

    # Extract date from filename
    snapshot_date = import_data.extract_date_from_filename(f.filename)
    if not snapshot_date:
        return jsonify({"error": f"Could not extract a date from filename '{f.filename}'. Expected format: something_YYYY-MM-DD.csv"}), 400

    # Check for duplicate
    existing_dates = {s["snapshot_date"] for s in db.get_snapshots()}
    if snapshot_date in existing_dates:
        return jsonify({"error": f"Date {snapshot_date} is already imported. Delete the existing snapshot first if you want to re-import."}), 409

    # Save to a temp file so parse_csv can read it
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    try:
        f.save(tmp)
        tmp.close()

        positions = import_data.parse_csv(tmp.name)
        if not positions:
            return jsonify({"error": "No positions found in the CSV file. Check that the file is a valid myFund export."}), 400

        quarter = import_data.date_to_quarter(snapshot_date)
        snapshot_id = db.create_snapshot(quarter, snapshot_date)
        db.insert_positions(snapshot_id, positions)

        total_value = sum(p["value_pln"] for p in positions)
        return jsonify({
            "ok": True,
            "quarter": quarter,
            "snapshot_date": snapshot_date,
            "positions_count": len(positions),
            "total_value": total_value,
            "snapshot_id": snapshot_id,
        })
    finally:
        os.unlink(tmp.name)


@app.route("/api/import-cashflows", methods=["POST"])
def api_import_cashflows():
    """Import a myfund.pl 'Wkład i wartość' XLSX export.

    Wipes the cash_flows table and inserts every row from the file. The user's
    workflow is to re-export the full history each quarter, so idempotent
    replace-all is the simplest correct behavior.

    Expected columns (Polish from myfund.pl): Data, Operacja, Wartość, Waluta,
    Kurs, Wartość [PLN], Konto, Portfel.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "Please upload an .xlsx file"}), 400

    import openpyxl
    import tempfile

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    try:
        f.save(tmp)
        tmp.close()

        try:
            wb = openpyxl.load_workbook(tmp.name, read_only=True, data_only=True)
        except Exception as e:
            return jsonify({"error": f"Could not open as XLSX: {e}"}), 400

        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            return jsonify({"error": "File appears empty (no data rows)"}), 400

        # The parser below reads columns BY POSITION, so a reordered or shifted
        # column layout would silently import wrong numbers. Validate the header
        # row first and fail loudly instead.
        EXPECTED_HEADER = ("Data", "Operacja", "Wartość", "Waluta", "Kurs", "Wartość [PLN]", "Konto")
        header = tuple(str(h).strip() if h is not None else "" for h in rows[0][:7])
        if header != EXPECTED_HEADER:
            return jsonify({
                "error": (
                    f"Unexpected column layout: {list(header)}. "
                    f"Expected: {list(EXPECTED_HEADER)}. "
                    "Is this a myfund.pl 'Wkład i wartość' export?"
                )
            }), 400

        # Map operation strings to canonical values
        OP_MAP = {
            "Wpłata automatyczna": "deposit",
            "Wypłata automatyczna": "withdrawal",
        }

        events = []
        skipped = 0
        for row in rows[1:]:
            if not row or row[0] is None:
                continue
            data, operacja, wartosc, waluta, kurs, wartosc_pln, konto = row[:7]

            op = OP_MAP.get(operacja)
            if op is None:
                skipped += 1
                continue
            if wartosc_pln is None:
                skipped += 1
                continue

            # Date can be a datetime object (most common) or a string
            if hasattr(data, "strftime"):
                event_date = data.strftime("%Y-%m-%d")
            else:
                event_date = str(data)[:10]

            events.append({
                "event_date": event_date,
                "operation": op,
                "value_pln": float(wartosc_pln),
                "currency": waluta,
                "original_value": float(wartosc) if wartosc is not None else None,
                "account": konto,
            })

        if not events:
            return jsonify({"error": "No valid rows found. Are the operation labels in Polish?"}), 400

        db.replace_cash_flows(events)
        summary = db.get_cash_flow_summary()

        return jsonify({
            "ok": True,
            "imported": len(events),
            "skipped": skipped,
            "deposited": summary["deposited"],
            "withdrawn": summary["withdrawn"],
            "net_invested": summary["net_invested"],
            "earliest_date": summary["earliest_date"],
            "latest_date": summary["latest_date"],
        })
    finally:
        os.unlink(tmp.name)


# --- Retirement planner ---------------------------------------------------
#
# Projection only. It computes the arithmetic consequences of assumptions the
# user supplies and makes no recommendation. Every Polish rule is a stored
# setting rather than a constant, because limits and rates change annually.

# Defaults are a starting point, NOT authority. The UI labels them as needing
# verification against current rules, and they are stored per-user on first
# save so a stale default here cannot silently override a corrected value.
RETIREMENT_DEFAULTS = {
    "current_age": 40,
    "retirement_age": 60,
    "horizon_age": 90,
    "annual_spending": 120000,
    "annual_savings": 100000,
    "ike_access_age": 60,
    "ikze_access_age": 65,
    "belka_rate": 0.19,
    "ikze_withdrawal_rate": 0.10,
    "ike_annual_limit": 26019,
    "ikze_annual_limit": 10407,
    "zus_annual": 0,
    "zus_start_age": 65,
    "ppk_enabled": 0,
    "ppk_gross_salary": 0,
    "ppk_employee_rate": 0.02,
    "ppk_employer_rate": 0.015,
    "ppk_state_annual": 240,
    "ppk_access_age": 60,
    "ppk_lump_sum_fraction": 0.25,
    "ppk_installment_years": 10,
    "start_ppk": 0,
    "inflation_rate": 0.035,
    "expected_real_return": 0.05,
    "use_historical_returns": 1,
    "success_threshold": 0.90,
}

# Which tax wrapper each account belongs to. Mirrors getRetirementType() in
# static/common.js — IKE-M is maklerskie IKE and shares its tax treatment.
IKE_ACCOUNT_MARKERS = ("IKE-M", "IKE OBLIGACJE", "IKE")


def _classify_account(account):
    """Return 'ike', 'ikze' or 'taxable' for an account name."""
    if not account:
        return "taxable"
    upper = account.upper()
    if "IKZE" in upper:
        return "ikze"
    if any(marker in upper for marker in IKE_ACCOUNT_MARKERS):
        return "ike"
    return "taxable"


def _current_balances(data):
    """Split the newest snapshot across wrappers, with an estimated cost basis.

    Basis matters because Belka is charged on gains only. Per-account basis
    isn't tracked, so the portfolio-wide ratio (net invested / current value)
    from the cash-flow import is applied to every bucket. Approximate, and
    stated as such in the UI.
    """
    latest = data["latest"]
    balances = {"taxable": 0.0, "ike": 0.0, "ikze": 0.0}
    if latest:
        for p in data["all_positions"].get(latest["id"], []):
            balances[_classify_account(p["account"])] += p["value_pln"]

    # Cash is spendable immediately, so it belongs with the taxable pot.
    balances["taxable"] += data["cash_total"]

    lifetime = data["lifetime"]
    total = sum(balances.values())
    if lifetime.get("available") and total > 0:
        basis_ratio = min(1.0, max(0.0, lifetime["net_invested"] / total))
    else:
        basis_ratio = 1.0   # unknown basis -> assume no taxable gain

    return balances, basis_ratio


def _real_return_pool(data, inflation_rate, size=2000, seed=12345):
    """Annual REAL returns to bootstrap from.

    Quarterly net-worth market returns (contributions removed, as on the
    forecast page) are deflated to real terms, then sampled in groups of four
    and compounded. Sampling quarters rather than whole years keeps the sample
    size usable — 18 quarters would otherwise yield only four annual figures.
    """
    timeline = data["timeline"]
    quarterly = []
    for i in range(1, len(timeline)):
        prev, curr = timeline[i - 1], timeline[i]
        prev_nw = prev["portfolio_total"] + prev["cash_total"] - prev["mortgage_total"]
        curr_nw = curr["portfolio_total"] + curr["cash_total"] - curr["mortgage_total"]
        if prev_nw <= 0:
            continue
        contrib = curr.get("net_contributions") or 0
        quarterly.append((curr_nw - prev_nw - contrib) / prev_nw)

    if not quarterly:
        return None

    q_inflation = (1 + inflation_rate) ** 0.25 - 1
    real_q = [(1 + r) / (1 + q_inflation) - 1 for r in quarterly]

    rng = random.Random(seed)
    pool = []
    for _ in range(size):
        year = 1.0
        for _ in range(4):
            year *= (1 + real_q[rng.randrange(len(real_q))])
        pool.append(year - 1)
    return pool


def _retirement_params(settings, data):
    """Merge stored settings with live portfolio balances into engine params."""
    def num(key):
        raw = settings.get(key, RETIREMENT_DEFAULTS[key])
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(RETIREMENT_DEFAULTS[key])

    balances, basis_ratio = _current_balances(data)

    params = {k: num(k) for k in RETIREMENT_DEFAULTS}
    params["ppk_enabled"] = bool(num("ppk_enabled"))
    params["ppk_installment_years"] = int(num("ppk_installment_years"))

    params.update({
        "start_taxable": balances["taxable"],
        "start_taxable_basis": balances["taxable"] * basis_ratio,
        "start_ike": balances["ike"],
        "start_ike_basis": balances["ike"] * basis_ratio,
        "start_ikze": balances["ikze"],
        "start_ikze_basis": balances["ikze"] * basis_ratio,
        "start_ppk": num("start_ppk"),
    })
    return params, balances, basis_ratio


@app.route("/retirement")
def retirement_page():
    return render_template("retirement.html")


@app.route("/api/retirement", methods=["GET"])
def api_retirement():
    """Run the planner with the stored settings."""
    data = _build_dashboard_data()
    if not data["timeline"]:
        return jsonify({"available": False, "reason": "No snapshots yet"})

    settings = db.get_retirement_settings()
    params, balances, basis_ratio = _retirement_params(settings, data)

    if params["use_historical_returns"]:
        returns = _real_return_pool(data, params["inflation_rate"])
        return_source = "historical (real)"
    else:
        returns = None
        return_source = "fixed"
    if not returns:
        returns = [params["expected_real_return"]]
        return_source = "fixed"

    threshold = params["success_threshold"]
    age, rate = retirement.earliest_feasible_age(
        params, returns, threshold=threshold, paths=300,
        min_age=int(params["current_age"]) + 1, max_age=75, seed=42)

    chosen = retirement.success_rate(params, returns, paths=300, seed=42)
    sustainable = retirement.sustainable_spending(
        params, returns, threshold=threshold, paths=200, seed=42)
    path = retirement.median_path(params, returns, paths=200, seed=42)

    return jsonify({
        "available": True,
        "settings": {k: settings.get(k, v) for k, v in RETIREMENT_DEFAULTS.items()},
        "balances": balances,
        "basis_ratio": round(basis_ratio, 3),
        "return_source": return_source,
        "mean_real_return": round(sum(returns) / len(returns), 4),
        "earliest_feasible_age": age,
        "earliest_feasible_rate": round(rate, 3),
        "chosen_age_success_rate": round(chosen, 3),
        "sustainable_spending_at_chosen_age": round(sustainable, -2),
        "path": path,
    })


@app.route("/api/retirement", methods=["POST"])
def api_save_retirement():
    payload = request.get_json() or {}
    # Only persist keys the planner knows about, so a malformed request can't
    # pollute the settings table.
    known = {k: v for k, v in payload.items() if k in RETIREMENT_DEFAULTS}
    if known:
        db.save_retirement_settings(known)
    return jsonify({"ok": True, "saved": sorted(known)})


def create_app():
    """Production entrypoint used by gunicorn (`gunicorn 'app:create_app()'`).

    Just initializes the DB and returns the module-level Flask app. Defining
    this lets the systemd unit on the Pi mirror the watchlist setup exactly.
    """
    db.init_db()
    return app


if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, port=5001)
