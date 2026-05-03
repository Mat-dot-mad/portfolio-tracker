import json
import os
from datetime import date

from flask import Flask, render_template, request, redirect, url_for, jsonify, make_response, session

import db
import nbp
import import_data

app = Flask(__name__)
# SECRET_KEY signs the session cookie so the browser cannot forge "authenticated".
# In production it comes from /etc/portfolio.env (32-byte hex from secrets.token_hex).
# A fixed dev fallback is fine because dev mode usually has DASHBOARD_PASSWORD unset
# (auth disabled), so cookie integrity doesn't matter there.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-for-production")


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
        if expected and submitted == expected:
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
    """Builds the full dashboard data payload (shared by /api/dashboard and /api/export-html)."""
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
    }


@app.route("/api/dashboard")
def api_dashboard():
    """Returns all data needed by the single-page dashboard."""
    return jsonify(_build_dashboard_data())


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


@app.route("/api/export-html")
def export_html():
    """Generate a self-contained HTML file with the full dashboard + compare view embedded."""
    data = _build_dashboard_data()

    # Serialize the data to JSON. Escape "</" so an embedded "</script>" cannot
    # break out of the inline script tag.
    data_json = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")

    # Read the existing JS files so we can inline them in the export.
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    with open(os.path.join(static_dir, "app.js"), encoding="utf-8") as f:
        app_js = f.read()
    with open(os.path.join(static_dir, "compare.js"), encoding="utf-8") as f:
        compare_js = f.read()

    # Disable the auto-init calls at the bottom of each file. The export
    # template provides its own init that uses embedded data instead of fetch().
    app_js = app_js.replace("\nloadDashboard();", "\n// loadDashboard(); // disabled in static export")
    compare_js = compare_js.replace("\nloadCompare();", "\n// loadCompare(); // disabled in static export")

    # Strip duplicate top-level const from compare.js — app.js already declares
    # the same `const IKE_M_ACCOUNTS` in the shared global scope, and two const
    # declarations of the same name across classic <script> tags throw a
    # SyntaxError that silently breaks compare.js.
    compare_js = compare_js.replace(
        "const IKE_M_ACCOUNTS = ['IKE-M', 'IKE OBLIGACJE'];",
        "// IKE_M_ACCOUNTS reused from app.js in static export",
    )

    html = render_template(
        "export.html",
        dashboard_json=data_json,
        app_js=app_js,
        compare_js=compare_js,
        export_date=date.today().isoformat(),
    )

    response = make_response(html)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="portfolio_{date.today().isoformat()}.html"'
    )
    return response


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
