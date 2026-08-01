"""Shared pytest fixtures.

Every test runs against a throwaway SQLite file, never the real portfolio.db.
db.get_db() looks up the module-level DB_PATH at call time, so monkeypatching
db.DB_PATH is enough to redirect all database access — no production code needs
a test-only hook.
"""

import os
import sys

import pytest

# Make the application modules importable when pytest runs from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import app as app_module  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """An empty database with the schema applied, isolated per test."""
    path = tmp_path / "test_portfolio.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    return path


@pytest.fixture
def client(temp_db, monkeypatch):
    """Flask test client backed by the isolated database, auth disabled.

    Auth is off by default (DASHBOARD_PASSWORD unset) so most tests can hit
    endpoints directly; the auth tests set it explicitly.
    """
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


# ── Helpers for building fixture data ────────────────

@pytest.fixture
def make_snapshot():
    """Create a snapshot with optional positions and manual entries.

    Returns the snapshot id so tests can build multi-quarter histories.
    """
    def _make(quarter, snapshot_date, portfolio=0.0, cash=0.0, mortgage=0.0,
              ppk=0.0):
        snapshot_id = db.create_snapshot(quarter, snapshot_date)
        if portfolio:
            db.insert_positions(snapshot_id, [{
                "name": "Test Position",
                "ticker": "TEST",
                "isin": None,
                "account": "Test Account",
                "group_name": "Test Group",
                "currency": "PLN",
                "tags": "TestTag",
                "value_pln": portfolio,
            }])
        entries = []
        if cash:
            entries.append({"type": "cash", "label": "Cash", "currency": "PLN",
                            "original_amount": cash, "amount_pln": cash})
        if mortgage:
            entries.append({"type": "mortgage", "label": "Mortgage", "currency": "PLN",
                            "original_amount": mortgage, "amount_pln": mortgage})
        if ppk:
            entries.append({"type": "ppk", "label": "PPK", "currency": "PLN",
                            "original_amount": ppk, "amount_pln": ppk})
        if entries:
            db.save_manual_entries(snapshot_id, entries)
        return snapshot_id
    return _make


@pytest.fixture
def make_cash_flows():
    """Replace the cash_flows table with events built from (date, op, amount) tuples."""
    def _make(*events):
        db.replace_cash_flows([
            {
                "event_date": date,
                "operation": operation,
                "value_pln": amount,
                "currency": "PLN",
                "original_value": amount,
                "account": "Test Account",
            }
            for date, operation, amount in events
        ])
    return _make
