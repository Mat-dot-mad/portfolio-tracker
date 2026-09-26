"""Transactional import journal and guarded, previewed undo of each dataset."""
import hashlib
import json
from datetime import datetime, timezone

from flask import current_app, jsonify, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

import db


def stamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def capture(conn, kind, snapshot_date=None):
    if kind == 'cashflows':
        coverage = conn.execute('SELECT * FROM cash_flow_coverage WHERE id=1').fetchone()
        return dict(records=[dict(r) for r in conn.execute('SELECT * FROM cash_flows ORDER BY id')],
                    coverage=dict(coverage) if coverage else None)
    snapshot = conn.execute('SELECT * FROM quarterly_snapshots WHERE snapshot_date=?', (snapshot_date,)).fetchone()
    return dict(snapshot=dict(snapshot) if snapshot else None,
                records=[dict(r) for r in conn.execute('SELECT * FROM positions WHERE snapshot_id=? ORDER BY id',
                                                      (snapshot['id'],))] if snapshot else [])


def record(conn, kind, snapshot_date, filename, raw, before, summary):
    after = capture(conn, kind, snapshot_date)
    return conn.execute('''INSERT INTO import_history
        (kind,snapshot_date,filename,file_hash,imported_at,summary,before_data,after_data)
        VALUES (?,?,?,?,?,?,?,?)''',
        (kind, snapshot_date, filename.replace('\\', '/').rsplit('/', 1)[-1],
         hashlib.sha256(raw).hexdigest(), stamp(), json.dumps(summary),
         json.dumps(before), json.dumps(after))).lastrowid


def undo_reason(conn, row, current):
    if row['undone_at']:
        return 'Already undone.'
    newer = conn.execute('''SELECT id FROM import_history WHERE kind=? AND snapshot_date IS ?
                           AND id>? AND undone_at IS NULL LIMIT 1''',
                         (row['kind'], row['snapshot_date'], row['id'])).fetchone()
    if newer:
        return 'Undo the newer import for this data first.'
    expected = json.loads(row['after_data'])
    # A later coverage confirmation is displayed explicitly in the preview;
    # undo restores the confirmation belonging to the previous flow history.
    matches = (current['records'] == expected['records'] if row['kind'] == 'cashflows'
               else current == expected)
    if not matches:
        return 'This data changed outside this import. Undo is unavailable.'
    before = json.loads(row['before_data'])
    if row['kind'] == 'csv' and before['snapshot'] is None and current['snapshot']:
        if conn.execute('SELECT 1 FROM manual_entries WHERE snapshot_id=? LIMIT 1',
                        (current['snapshot']['id'],)).fetchone():
            return 'This import created a quarter that now has saved balances. Replace its CSV to correct positions without deleting those balances.'
    return None


def describe(kind, state):
    records = state['records']
    if kind == 'csv':
        return dict(count=len(records), total=sum(r['value_pln'] for r in records),
                    snapshot_exists=state['snapshot'] is not None)
    dates = [r['event_date'] for r in records]
    return dict(count=len(records), deposits=sum(r['value_pln'] for r in records if r['operation']=='deposit'),
                withdrawals=sum(r['value_pln'] for r in records if r['operation']=='withdrawal'),
                first=min(dates, default=None), last=max(dates, default=None),
                confirmed_through=state['coverage']['confirmed_through'] if state['coverage'] else None)


def list_history():
    # Page by immutable id so a newly completed import does not shift pages.
    try:
        before_id = int(request.args.get('before', '9223372036854775807'))
        if not 0 < before_id <= 9223372036854775807:
            raise ValueError
    except ValueError:
        return jsonify(error='Invalid history page.'), 400
    with db.get_db() as conn:
        conn.execute('BEGIN')
        rows = conn.execute('SELECT * FROM import_history WHERE id<? ORDER BY id DESC LIMIT 26',
                            (before_id,)).fetchall()
        items = []
        for row in rows[:25]:
            current = capture(conn, row['kind'], row['snapshot_date'])
            item = {key:row[key] for key in ('id','kind','snapshot_date','filename','imported_at','undone_at')}
            item.update(summary=json.loads(row['summary']), undo_reason=undo_reason(conn, row, current))
            items.append(item)
    return jsonify(items=items, next_before=items[-1]['id'] if len(rows)>25 else None)


def insert_rows(conn, table, records):
    # Only trusted journal rows and fixed table names reach this helper.
    for row in records:
        conn.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',
                     tuple(row.values()))


def undo(import_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error='Provide an undo preview or confirmation.'), 400
    preview = payload.get('preview') is True
    signer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='import-undo')
    with db.get_db() as conn:
        conn.execute('BEGIN' if preview else 'BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM import_history WHERE id=?', (import_id,)).fetchone()
        if row is None:
            return jsonify(error='Import not found.'), 404
        current = capture(conn, row['kind'], row['snapshot_date'])
        reason = undo_reason(conn, row, current)
        if reason:
            return jsonify(error=reason), 409
        before = json.loads(row['before_data'])
        reviewed = dict(id=import_id, state=fingerprint(current))
        result = dict(id=import_id, kind=row['kind'], filename=row['filename'], snapshot_date=row['snapshot_date'],
                      current=describe(row['kind'], current), restored=describe(row['kind'], before))
        if preview:
            return jsonify(**result, preview_token=signer.dumps(reviewed))
        try:
            signed = signer.loads(payload.get('preview_token', ''), max_age=3600)
        except (BadSignature, TypeError):
            return jsonify(error='Undo preview expired or invalid. Preview again.'), 409
        if signed != reviewed:
            return jsonify(error='Data changed since the undo preview. Preview again.'), 409
        if row['kind'] == 'cashflows':
            conn.execute('DELETE FROM cash_flows')
            conn.execute('DELETE FROM cash_flow_coverage')
            insert_rows(conn, 'cash_flows', before['records'])
            if before['coverage']:
                insert_rows(conn, 'cash_flow_coverage', [before['coverage']])
        else:
            sid = current['snapshot']['id']
            if before['snapshot'] is None:
                # No manual balances (checked above). Derived commentary belongs
                # to the removed snapshot and cascades with it.
                conn.execute('DELETE FROM quarterly_snapshots WHERE id=?', (sid,))
            else:
                conn.execute('DELETE FROM positions WHERE snapshot_id=?', (sid,))
                insert_rows(conn, 'positions', before['records'])
        conn.execute('UPDATE import_history SET undone_at=? WHERE id=?', (stamp(), import_id))
    return jsonify(ok=True, **result)
