"""Report recorded inputs without inferring missing balances or export coverage."""
from datetime import date, timedelta

MANUAL_TYPES = ('cash', 'ppk', 'mortgage')


def summarize(snapshots, manual_entries, flows, coverage, today=None):
    today = today or date.today()
    grouped = {}
    for entry in manual_entries:
        grouped.setdefault((entry['snapshot_id'], entry['type']), []).append(entry['amount_pln'])
    rows = []
    for snapshot in snapshots:
        states = {}
        for kind in MANUAL_TYPES:
            values = grouped.get((snapshot['id'], kind), [])
            states[kind] = ('missing' if not values else 'confirmed_zero' if sum(values) == 0 else 'recorded')
        rows.append(dict(id=snapshot['id'], quarter=snapshot['quarter'],
                         snapshot_date=snapshot['snapshot_date'], balances=states))
    dates = [e['event_date'] for e in flows]
    latest = rows[0] if rows else None
    last_quarter_end = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
    quarter_ids = set()
    for snapshot in snapshots:
        when = date.fromisoformat(snapshot['snapshot_date'])
        quarter_ids.add(when.year * 4 + (when.month - 1) // 3)
    gaps = [f'{q // 4}-Q{q % 4 + 1}' for q in range(min(quarter_ids), max(quarter_ids) + 1)
            if q not in quarter_ids] if quarter_ids else []
    confirmed = coverage.get('confirmed_through')
    return dict(
        holdings_as_of=latest['snapshot_date'] if latest else None,
        last_completed_quarter_end=last_quarter_end.isoformat(),
        holdings_behind=bool(latest and latest['snapshot_date'] < last_quarter_end.isoformat()),
        future_snapshot=bool(latest and latest['snapshot_date'] > today.isoformat()),
        missing_quarters=gaps,
        snapshots=rows,
        cash_flows=dict(count=len(flows), first_event=min(dates, default=None),
                        last_event=max(dates, default=None), confirmed_through=confirmed,
                        revision=coverage['revision'],
                        covers_snapshot=bool(latest and confirmed and confirmed >= latest['snapshot_date'])),
    )
