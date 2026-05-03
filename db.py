import sqlite3
import os
from datetime import datetime

# DB location: in production the Pi's systemd unit sets DATABASE_PATH to
# /var/lib/portfolio/portfolio.db; locally we fall back to a file next to the code.
DB_PATH = os.environ.get(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(__file__), "portfolio.db"),
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS quarterly_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quarter TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES quarterly_snapshots(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                ticker TEXT,
                isin TEXT,
                account TEXT,
                group_name TEXT,
                currency TEXT,
                tags TEXT,
                value_pln REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS manual_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES quarterly_snapshots(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                label TEXT,
                currency TEXT NOT NULL DEFAULT 'PLN',
                original_amount REAL NOT NULL DEFAULT 0,
                amount_pln REAL NOT NULL
            );

            -- Contribution / withdrawal events imported from myfund.pl XLSX export.
            -- Wiped and re-inserted on every import (idempotent re-import workflow).
            CREATE TABLE IF NOT EXISTS cash_flows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date TEXT NOT NULL,           -- YYYY-MM-DD
                operation TEXT NOT NULL,            -- 'deposit' or 'withdrawal'
                value_pln REAL NOT NULL,
                currency TEXT,
                original_value REAL,
                account TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_cash_flows_date ON cash_flows(event_date);
        """)


def create_snapshot(quarter, snapshot_date):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO quarterly_snapshots (quarter, snapshot_date, created_at) VALUES (?, ?, ?)",
            (quarter, snapshot_date, datetime.now().isoformat()),
        )
        return cur.lastrowid


def insert_positions(snapshot_id, positions):
    with get_db() as conn:
        conn.executemany(
            """INSERT INTO positions
               (snapshot_id, name, ticker, isin, account, group_name, currency, tags, value_pln)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    p["name"],
                    p.get("ticker"),
                    p.get("isin"),
                    p.get("account"),
                    p.get("group_name"),
                    p.get("currency"),
                    p.get("tags"),
                    p["value_pln"],
                )
                for p in positions
            ],
        )


def save_manual_entries(snapshot_id, entries):
    """Replace all manual entries for a snapshot with new ones."""
    with get_db() as conn:
        conn.execute("DELETE FROM manual_entries WHERE snapshot_id = ?", (snapshot_id,))
        conn.executemany(
            """INSERT INTO manual_entries
               (snapshot_id, type, label, currency, original_amount, amount_pln)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    e["type"],
                    e.get("label", ""),
                    e.get("currency", "PLN"),
                    e.get("original_amount", e["amount_pln"]),
                    e["amount_pln"],
                )
                for e in entries
            ],
        )


def get_manual_entries(snapshot_id):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM manual_entries WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_snapshots():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM quarterly_snapshots ORDER BY snapshot_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_snapshot(snapshot_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM quarterly_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
    return dict(row) if row else None


def get_positions(snapshot_id):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE snapshot_id = ? ORDER BY tags, account, name",
            (snapshot_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_snapshot(snapshot_id):
    with get_db() as conn:
        conn.execute("DELETE FROM positions WHERE snapshot_id = ?", (snapshot_id,))
        conn.execute("DELETE FROM manual_entries WHERE snapshot_id = ?", (snapshot_id,))
        conn.execute("DELETE FROM quarterly_snapshots WHERE id = ?", (snapshot_id,))


def get_all_snapshots_summary():
    """Returns aggregated totals for each snapshot (portfolio + manual entries)."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                s.id,
                s.quarter,
                s.snapshot_date,
                COALESCE(p.total, 0) AS portfolio_total,
                COALESCE(mc.total, 0) AS cash_total,
                COALESCE(mm.total, 0) AS mortgage_total
            FROM quarterly_snapshots s
            LEFT JOIN (
                SELECT snapshot_id, SUM(value_pln) AS total
                FROM positions GROUP BY snapshot_id
            ) p ON p.snapshot_id = s.id
            LEFT JOIN (
                SELECT snapshot_id, SUM(amount_pln) AS total
                FROM manual_entries WHERE type = 'cash' GROUP BY snapshot_id
            ) mc ON mc.snapshot_id = s.id
            LEFT JOIN (
                SELECT snapshot_id, SUM(amount_pln) AS total
                FROM manual_entries WHERE type = 'mortgage' GROUP BY snapshot_id
            ) mm ON mm.snapshot_id = s.id
            ORDER BY s.snapshot_date ASC
        """).fetchall()

    return [
        {
            "id": r["id"],
            "quarter": r["quarter"],
            "snapshot_date": r["snapshot_date"],
            "portfolio_total": r["portfolio_total"],
            "cash_total": r["cash_total"],
            "mortgage_total": r["mortgage_total"],
            "net_worth": r["portfolio_total"] + r["cash_total"] - r["mortgage_total"],
        }
        for r in rows
    ]


# ── Cash flow helpers ────────────────────────────────────────────────

def replace_cash_flows(events):
    """Wipe the cash_flows table and bulk-insert all events.

    Idempotent re-import: caller passes the full set of events parsed from the
    latest XLSX export, and we replace everything. Avoids deduplication
    headaches when the source has no stable per-row ID.

    `events` is an iterable of dicts with keys:
        event_date (YYYY-MM-DD str), operation ('deposit'|'withdrawal'),
        value_pln (float), currency (str|None), original_value (float|None),
        account (str|None)
    """
    with get_db() as conn:
        conn.execute("DELETE FROM cash_flows")
        conn.executemany(
            """INSERT INTO cash_flows
               (event_date, operation, value_pln, currency, original_value, account)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    e["event_date"],
                    e["operation"],
                    e["value_pln"],
                    e.get("currency"),
                    e.get("original_value"),
                    e.get("account"),
                )
                for e in events
            ],
        )


def get_cash_flow_summary():
    """Return lifetime totals across all cash flows.

    Returns dict: { count, deposited, withdrawn, net_invested, earliest_date, latest_date }
    or zeros if the table is empty.
    """
    with get_db() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(CASE WHEN operation='deposit' THEN value_pln END), 0) AS deposited,
                COALESCE(SUM(CASE WHEN operation='withdrawal' THEN value_pln END), 0) AS withdrawn,
                MIN(event_date) AS earliest_date,
                MAX(event_date) AS latest_date
            FROM cash_flows
        """).fetchone()

    deposited = row["deposited"]
    withdrawn = row["withdrawn"]
    return {
        "count": row["count"],
        "deposited": deposited,
        "withdrawn": withdrawn,
        "net_invested": deposited - withdrawn,
        "earliest_date": row["earliest_date"],
        "latest_date": row["latest_date"],
    }


def get_net_contributions_by_period(period_starts):
    """Net contributions (deposits − withdrawals) summed within each period.

    `period_starts` is a list of date strings (YYYY-MM-DD), one per snapshot,
    in ascending order. The function returns a list of the same length where:

      result[0] = sum of all events with event_date <= period_starts[0]
                  (i.e. all pre-snapshot history rolled into the first snapshot,
                  per the user's preference)
      result[i] = sum of events with period_starts[i-1] < event_date <= period_starts[i]
                  (i.e. events strictly after the previous snapshot, up to and
                  including this one)
    """
    if not period_starts:
        return []

    with get_db() as conn:
        rows = conn.execute(
            "SELECT event_date, operation, value_pln FROM cash_flows ORDER BY event_date ASC"
        ).fetchall()

    totals = [0.0] * len(period_starts)
    # Convert period_starts to a sorted list and find the bucket for each event.
    # Linear scan with two pointers is fine for typical dataset size.
    bucket_idx = 0
    for r in rows:
        ed = r["event_date"]
        # Advance bucket_idx until period_starts[bucket_idx] >= ed
        while bucket_idx < len(period_starts) - 1 and ed > period_starts[bucket_idx]:
            bucket_idx += 1
        # Edge: if event_date is later than the last snapshot, drop it (after-the-fact data)
        if ed > period_starts[-1]:
            continue
        signed = r["value_pln"] if r["operation"] == "deposit" else -r["value_pln"]
        totals[bucket_idx] += signed

    return totals
