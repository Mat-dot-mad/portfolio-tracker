"""Validate and preview uploads before atomically saving their reviewed contents."""
import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import date, datetime

import openpyxl
from flask import current_app, jsonify, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

import db
import import_data

HEADER = ("Data", "Operacja", "Wartość", "Waluta", "Kurs", "Wartość [PLN]", "Konto")
POSITION_KEYS = ("name", "ticker", "isin", "account", "group_name", "currency", "tags", "value_pln")
FLOW_KEYS = ("event_date", "operation", "value_pln", "currency", "original_value", "account")


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("missing or invalid amount")
    try:
        result = float(str(value).replace("\xa0", "").replace(" ", ""))
    except (TypeError, ValueError):
        raise ValueError(f"unreadable amount: {value!r}") from None
    if not math.isfinite(result):
        raise ValueError("amount must be finite")
    return result


def parse_positions(raw):
    text = raw.decode("cp1250").replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not {"Walor", "Wartość waloru [PLN]"}.issubset(reader.fieldnames or []):
        raise ValueError("Missing required CSV columns: Walor and Wartość waloru [PLN]")
    positions, warnings, errors = [], [], []
    for line, row in enumerate(reader, 2):
        name = (row.get("Walor") or "").strip()
        if not name or name.startswith("Razem"):
            warnings.append(f"Row {line}: ignored blank or totals row")
            continue
        try:
            if None in row:
                raise ValueError("extra columns; check delimiters")
            amount = number(row.get("Wartość waloru [PLN]"))
        except ValueError as exc:
            errors.append(f"Row {line} ({name}): {exc}")
            continue
        if amount == 0:
            warnings.append(f"Row {line} ({name}): ignored zero-value position")
            continue
        if amount < 0:
            warnings.append(f"Row {line} ({name}): negative position value; verify it is intentional")
        account = (row.get("Konto") or "").strip() or None
        positions.append(dict(name=name, ticker=import_data.extract_ticker(name, account),
                              isin=(row.get("ISIN") or "").strip() or None,
                              account=account, group_name=import_data.clean_group_name(row.get("Grupa")),
                              currency=row.get("Waluta waloru"), tags=row.get("Tagi"), value_pln=amount))
    return positions, warnings, errors


def parse_flows(raw):
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f"Could not open as XLSX: {exc}") from exc
    events, warnings, errors = [], [], []
    try:
        rows = workbook.active.iter_rows(values_only=True)
        header = next(rows, ())
        if tuple(str(v).strip() if v is not None else "" for v in header[:7]) != HEADER:
            raise ValueError("Unexpected column layout. Expected: " + ", ".join(HEADER))
        for line, row in enumerate(rows, 2):
            if not any(v is not None for v in row):
                continue
            values = list(row[:7]) + [None] * max(0, 7 - len(row))
            when, operation, original, currency, _rate, amount, account = values
            op = {"Wpłata automatyczna": "deposit", "Wypłata automatyczna": "withdrawal"}.get(operation)
            if not op:
                warnings.append(f"Row {line}: ignored unrecognized operation {operation!r}")
                continue
            try:
                event_date = when.date().isoformat() if isinstance(when, datetime) else date.fromisoformat(str(when)).isoformat()
                amount = number(amount)
                original = number(original) if original is not None else None
                if amount < 0 or (original is not None and original < 0):
                    raise ValueError("use positive amounts; the operation defines deposit or withdrawal")
                events.append(dict(event_date=event_date, operation=op, value_pln=amount,
                                   currency=currency, original_value=original, account=account))
            except (TypeError, ValueError) as exc:
                errors.append(f"Row {line}: {exc}")
    finally:
        workbook.close()
    return events, warnings, errors


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def flow_summary(events):
    deposited = sum(e["value_pln"] for e in events if e["operation"] == "deposit")
    withdrawn = sum(e["value_pln"] for e in events if e["operation"] == "withdrawal")
    dates = [e["event_date"] for e in events]
    return dict(count=len(events), deposited=deposited, withdrawn=withdrawn,
                net_invested=deposited - withdrawn, earliest_date=min(dates, default=None),
                latest_date=max(dates, default=None))


def handle_upload(kind):
    upload = request.files.get("file")
    suffix = ".csv" if kind == "csv" else ".xlsx"
    if upload is None or not upload.filename or not upload.filename.lower().endswith(suffix):
        return jsonify(error=f"Please upload a {suffix} file"), 400
    preview = request.form.get("preview") == "1"
    snapshot_date = None
    if kind == "csv":
        try:
            snapshot_date = date.fromisoformat(import_data.extract_date_from_filename(upload.filename) or "").isoformat()
        except ValueError:
            return jsonify(error="Filename must contain a valid date (YYYY-MM-DD)"), 400
        # Legacy callers cannot accidentally replace an existing quarter.
        if not preview and request.form.get("replace") != "1" and any(s["snapshot_date"] == snapshot_date for s in db.get_snapshots()):
            return jsonify(error=f"Date {snapshot_date} is already imported. Preview and choose Replace snapshot."), 409
    raw = upload.read()
    try:
        records, warnings, errors = (parse_positions(raw) if kind == "csv" else parse_flows(raw))
    except (ValueError, UnicodeError) as exc:
        return jsonify(error=str(exc)), 400
    if not records and not errors:
        errors.append("No valid rows found in the file")
    if errors:
        return jsonify(error="Fix the invalid rows before saving.", errors=errors, warnings=warnings), 400

    signer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="import-preview")
    file_hash = fingerprint([kind, snapshot_date, hashlib.sha256(raw).hexdigest()])
    with db.get_db() as conn:
        # Hold the write lock through state validation and replacement. No other
        # importer can change the reviewed data between those two operations.
        if not preview:
            conn.execute("BEGIN IMMEDIATE")
        existing = None
        if kind == "csv":
            existing = conn.execute("SELECT * FROM quarterly_snapshots WHERE snapshot_date=?", (snapshot_date,)).fetchone()
            previous = conn.execute("SELECT * FROM quarterly_snapshots WHERE snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 1", (snapshot_date,)).fetchone()
            comparison = existing or previous
            old = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE snapshot_id=? ORDER BY id", (comparison["id"],))] if comparison else []
            state = fingerprint([dict(existing) if existing else None, dict(comparison) if comparison else None, old])
            key = lambda p: (p.get("isin") or p.get("ticker") or p["name"], p.get("account"))
            before, after = {key(p) for p in old}, {key(p) for p in records}
            result = dict(quarter=import_data.date_to_quarter(snapshot_date), snapshot_date=snapshot_date,
                          positions_count=len(records), total_value=sum(p["value_pln"] for p in records),
                          previous_total=sum(p["value_pln"] for p in old),
                          comparison_date=comparison["snapshot_date"] if comparison else None,
                          new_positions=[p["name"] for p in records if key(p) not in before],
                          removed_positions=[p["name"] for p in old if key(p) not in after],
                          replacing=existing is not None)
        else:
            old = [dict(r) for r in conn.execute("SELECT * FROM cash_flows ORDER BY id")]
            state = fingerprint(old)
            old_counts = Counter(tuple(e.get(k) for k in FLOW_KEYS) for e in old)
            new_counts = Counter(tuple(e.get(k) for k in FLOW_KEYS) for e in records)
            result = dict(flow_summary(records), imported=len(records), skipped=len(warnings),
                          previous=flow_summary(old), added_rows=sum((new_counts-old_counts).values()),
                          removed_rows=sum((old_counts-new_counts).values()), replacing=bool(old))
            if old and (result["earliest_date"] > result["previous"]["earliest_date"] or result["latest_date"] < result["previous"]["latest_date"]):
                warnings.append("The uploaded date range is shorter than the saved history. Verify this is a full export.")
        result.update(warnings=warnings, filename=upload.filename)
        if preview:
            result["preview_token"] = signer.dumps(dict(file=file_hash, state=state))
            return jsonify(result)
        token = request.form.get("preview_token")
        # Existing clients may import new data directly. Destructive position
        # replacement always requires a signed preview; the UI uses it for all saves.
        if token or (kind == "csv" and existing):
            try:
                reviewed = signer.loads(token or "", max_age=3600)
            except BadSignature:
                return jsonify(error="Preview expired or invalid. Preview the file again."), 409
            if reviewed != dict(file=file_hash, state=state):
                return jsonify(error="The file or saved data changed. Preview the file again."), 409
        if kind == "csv":
            if existing:
                sid = existing["id"]
                conn.execute("DELETE FROM positions WHERE snapshot_id=?", (sid,))
            else:
                sid = conn.execute("INSERT INTO quarterly_snapshots (quarter,snapshot_date,created_at) VALUES (?,?,?)", (result["quarter"], snapshot_date, datetime.now().isoformat())).lastrowid
            conn.executemany("INSERT INTO positions (snapshot_id," + ",".join(POSITION_KEYS) + ") VALUES (?,?,?,?,?,?,?,?,?)",
                             [(sid,) + tuple(p.get(k) for k in POSITION_KEYS) for p in records])
            result["snapshot_id"] = sid
        else:
            conn.execute("DELETE FROM cash_flows")
            conn.execute("DELETE FROM cash_flow_coverage")
            conn.executemany("INSERT INTO cash_flows (" + ",".join(FLOW_KEYS) + ") VALUES (?,?,?,?,?,?)",
                             [tuple(e.get(k) for k in FLOW_KEYS) for e in records])
    return jsonify(ok=True, **result)
