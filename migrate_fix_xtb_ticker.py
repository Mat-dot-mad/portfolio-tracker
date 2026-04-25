"""
One-time migration: fix ticker for positions where the CSV had no separate
ticker symbol (e.g. XTB). The ticker was incorrectly set to the account name
or a fragment of it.

This finds positions where the ticker looks like an account name and corrects
it to the first word of the position name.

Safe to run multiple times — only updates rows that need fixing.
"""

import db


def ticker_looks_like_account(ticker, account):
    """Check if the extracted ticker is actually the account name or part of it.

    We only flag it if the ticker is a substantial match — at least 3 characters.
    This avoids false positives like ticker 'V' matching 'Interactive Brokers'.
    """
    if not ticker or not account:
        return False
    if len(ticker) < 3:
        return False

    ticker_upper = ticker.upper()
    account_upper = account.upper()
    return account_upper in ticker_upper or ticker_upper in account_upper


def main():
    db.init_db()
    conn = db.get_db()

    rows = conn.execute("""
        SELECT id, name, ticker, account
        FROM positions
        WHERE ticker IS NOT NULL AND account IS NOT NULL
    """).fetchall()

    fixes = []
    for row in rows:
        if ticker_looks_like_account(row["ticker"], row["account"]):
            correct_ticker = row["name"].split()[0] if row["name"] else row["ticker"]
            if correct_ticker != row["ticker"]:
                fixes.append((correct_ticker, row["id"]))
                print(f'  FIX id={row["id"]}: "{row["ticker"]}" -> "{correct_ticker}" (name: {row["name"][:60]})')

    if fixes:
        conn.executemany("UPDATE positions SET ticker = ? WHERE id = ?", fixes)
        conn.commit()
        print(f"\nFixed {len(fixes)} positions.")
    else:
        print("No positions need fixing.")

    conn.close()


if __name__ == "__main__":
    main()
