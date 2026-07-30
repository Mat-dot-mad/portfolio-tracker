"""
One-time migration: recompute every stored ticker with the current
extract_ticker() logic.

Needed because myFund puts a qualifier before the ticker for some securities —
a share class '(Acc)' or a brand name '(Google)' — and the old parser took the
first parenthesised group. Five distinct ETFs all ended up stored as 'Acc',
making them indistinguishable in the breakdown accordion and the Compare diff.

Recomputes from the stored `name` and `account` rather than re-reading the CSV
exports, so it works on any database including the Pi's.

Safe to run repeatedly — only rows whose ticker actually changes are written.
Run with --dry-run first to preview.
"""

import sys
from collections import defaultdict

import db
import import_data


def main():
    dry_run = "--dry-run" in sys.argv

    db.init_db()
    conn = db.get_db()

    rows = conn.execute(
        "SELECT id, name, ticker, account FROM positions WHERE name IS NOT NULL"
    ).fetchall()

    fixes = []
    # Group by the change so the output is one line per security rather than
    # one per snapshot — the same holding repeats across every quarter.
    summary = defaultdict(int)
    for row in rows:
        new_ticker = import_data.extract_ticker(row["name"], row["account"])
        if new_ticker and new_ticker != row["ticker"]:
            fixes.append((new_ticker, row["id"]))
            summary[(row["ticker"], new_ticker, row["name"])] += 1

    if not fixes:
        print("All tickers already match the current parser — nothing to do.")
        conn.close()
        return

    print(f"{len(summary)} distinct securities affected, {len(fixes)} rows:\n")
    for (old, new, name), count in sorted(summary.items(), key=lambda kv: kv[0][1]):
        print(f"  {old!r} -> {new!r}   ({count} rows)  {name[:64]}")

    if dry_run:
        print("\n--dry-run: no changes written.")
    else:
        conn.executemany("UPDATE positions SET ticker = ? WHERE id = ?", fixes)
        conn.commit()
        print(f"\nUpdated {len(fixes)} rows.")

    conn.close()


if __name__ == "__main__":
    main()
