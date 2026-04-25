"""
Import CSV exports from myFund.pl into the portfolio database.

Usage:
    python import_data.py

Scans raw_data_exports/ for CSV files, extracts the date from the filename,
and imports positions into the database. Skips files already imported.
"""

import csv
import math
import os
import re
import sys

import db

RAW_DATA_DIR = os.path.join(os.path.dirname(__file__), "raw_data_exports")


def extract_date_from_filename(filename):
    """Extract a YYYY-MM-DD date from the filename.

    Example: 'myfund.pl_Mat_portfelSklad_2026-04-01.csv' -> '2026-04-01'
    """
    match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if not match:
        return None
    return match.group(1)


def date_to_quarter(date_str):
    """Convert a date string to a quarter label.

    Example: '2025-03-31' -> '2025-Q1'
    """
    month = int(date_str.split("-")[1])
    year = date_str.split("-")[0]
    quarter = math.ceil(month / 3)
    return f"{year}-Q{quarter}"


def parse_number(value):
    """Parse a number that may have spaces as thousand separators.

    Examples: '1 048 009.78' -> 1048009.78, '-3 281.83' -> -3281.83
    """
    if not value or not value.strip():
        return 0.0
    cleaned = value.replace("\xa0", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def extract_ticker(walor_name, account=None):
    """Extract ticker symbol from the Walor column.

    Stocks:  'NVIDIA Corporation (NVDA) (Interactive Brokers)' -> 'NVDA'
    Bonds:   'EDO1031 (2021-10-22) (Obligacje Skarbowe) (3.90%)' -> 'EDO1031'
    No ticker: 'XTB (BOSSA IKE)' -> 'XTB' (first word, because parens contain account)
    """
    # Find the first parenthesized value
    match = re.search(r"\(([^)]+)\)", walor_name)
    if not match:
        # No parentheses at all — use the first word
        return walor_name.split()[0] if walor_name else None

    first_paren = match.group(1)

    # If it looks like a date (2021-10-22) or percentage (3.90%), it's a bond
    # Fall back to the first word of the name
    if re.match(r"^\d{4}-\d{2}-\d{2}$", first_paren) or first_paren.endswith("%"):
        return walor_name.split()[0]

    # If the extracted value matches or contains the account name, the CSV
    # omitted the ticker (e.g. "XTB (BOSSA IKE)" or "XTB (XTB (PLN))").
    # Fall back to the first word of the name.
    # Only check if the extracted value is 3+ chars to avoid false positives
    # (e.g. ticker "V" for Visa matching "Interactive Brokers").
    if account and len(first_paren) >= 3:
        account_upper = account.upper()
        paren_upper = first_paren.upper()
        if account_upper in paren_upper or paren_upper in account_upper:
            return walor_name.split()[0]

    return first_paren


def clean_group_name(value):
    """Replace non-breaking spaces in group names.

    Example: 'Akcje\xa0GPW' -> 'Akcje GPW'
    """
    if not value:
        return value
    return value.replace("\xa0", " ")


def parse_csv(filepath):
    """Parse a myFund CSV export and return a list of position dicts."""
    positions = []

    # Pre-process: replace HTML entities like &gt; (which contains a literal
    # semicolon that breaks CSV parsing) with their decoded characters.
    with open(filepath, encoding="cp1250") as f:
        raw = f.read()
    raw = raw.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")

    import io
    reader = csv.DictReader(io.StringIO(raw), delimiter=";")

    for row in reader:
        walor = row.get("Walor", "")

        # Skip the totals row
        if walor.startswith("Razem"):
            continue

        # Skip empty rows
        if not walor.strip():
            continue

        value_pln = parse_number(
            row.get("Warto\u015b\u0107 waloru [PLN]", "")
        )

        # Skip positions with zero value
        if value_pln == 0:
            continue

        account = row.get("Konto", "").strip() or None

        positions.append({
                "name": walor,
                "ticker": extract_ticker(walor, account),
                "isin": row.get("ISIN", "").strip() or None,
                "account": account,
                "group_name": clean_group_name(row.get("Grupa", "").strip()) or None,
                "currency": row.get("Waluta waloru", "").strip() or None,
                "tags": row.get("Tagi", "").strip() or None,
                "value_pln": value_pln,
            })

    return positions


def get_existing_dates():
    """Return a set of snapshot_date values already in the database."""
    snapshots = db.get_snapshots()
    return {s["snapshot_date"] for s in snapshots}


def main():
    # Make sure the database tables exist
    db.init_db()

    # Check that raw_data_exports/ exists
    if not os.path.isdir(RAW_DATA_DIR):
        print(f"Error: Directory not found: {RAW_DATA_DIR}")
        sys.exit(1)

    # Find all CSV files
    csv_files = sorted(f for f in os.listdir(RAW_DATA_DIR) if f.endswith(".csv"))
    if not csv_files:
        print(f"No CSV files found in {RAW_DATA_DIR}")
        sys.exit(0)

    existing_dates = get_existing_dates()

    imported = 0
    skipped = 0

    for filename in csv_files:
        filepath = os.path.join(RAW_DATA_DIR, filename)

        # Extract date from filename
        snapshot_date = extract_date_from_filename(filename)
        if not snapshot_date:
            print(f"  SKIP {filename} — could not extract date from filename")
            skipped += 1
            continue

        # Skip if already imported
        if snapshot_date in existing_dates:
            print(f"  SKIP {filename} — {snapshot_date} already imported")
            skipped += 1
            continue

        # Parse the CSV
        quarter = date_to_quarter(snapshot_date)
        positions = parse_csv(filepath)

        if not positions:
            print(f"  SKIP {filename} — no positions found")
            skipped += 1
            continue

        # Save to database
        snapshot_id = db.create_snapshot(quarter, snapshot_date)
        db.insert_positions(snapshot_id, positions)

        total_value = sum(p["value_pln"] for p in positions)
        print(
            f"  OK   {filename} — {quarter} ({snapshot_date})"
            f" — {len(positions)} positions, total: {total_value:,.2f} PLN"
        )
        imported += 1

    print()
    print(f"Done. Imported: {imported}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
