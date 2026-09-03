"""Tests for the Flask API: dashboard aggregation, imports, and auth."""

import io

import openpyxl
import pytest

import db


def _xlsx_bytes(header, *rows):
    """Build an in-memory XLSX file with the given header and data rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


VALID_HEADER = ("Data", "Operacja", "Wartość", "Waluta", "Kurs", "Wartość [PLN]", "Konto", "Portfel")


class TestDashboardLifetime:
    def test_market_gains_ignore_deposits_made_after_the_last_snapshot(
        self, client, make_snapshot, make_cash_flows
    ):
        """Regression test.

        current_wealth is measured at the last snapshot, so the invested figure
        must be capped at the same date. Counting later deposits as money in —
        before they can show up as value — understated market gains.
        """
        make_snapshot("2026-Q1", "2026-03-31", portfolio=1000.0)
        make_cash_flows(
            ("2026-03-01", "deposit", 500.0),   # before the snapshot — counts
            ("2026-04-15", "deposit", 300.0),   # after it — must NOT count
        )

        lifetime = client.get("/api/dashboard").get_json()["lifetime"]

        assert lifetime["net_invested"] == pytest.approx(500.0)
        assert lifetime["market_gains"] == pytest.approx(500.0)
        # The raw deposit total still reports everything imported...
        assert lifetime["deposited"] == pytest.approx(800.0)
        # ...but the measured period ends at the snapshot, so the annualized
        # figure the frontend derives covers a consistent window.
        assert lifetime["latest_date"] == "2026-03-31"

    def test_market_gains_are_wealth_minus_invested(
        self, client, make_snapshot, make_cash_flows
    ):
        make_snapshot("2026-Q1", "2026-03-31", portfolio=1200.0, cash=300.0, mortgage=400.0)
        make_cash_flows(("2026-01-01", "deposit", 1000.0))

        lifetime = client.get("/api/dashboard").get_json()["lifetime"]

        # Mortgage is a liability, not money invested, so it stays out of both sides.
        assert lifetime["current_wealth"] == pytest.approx(1500.0)
        assert lifetime["market_gains"] == pytest.approx(500.0)

    def test_unavailable_before_any_cash_flows_are_imported(self, client, make_snapshot):
        make_snapshot("2026-Q1", "2026-03-31", portfolio=1000.0)
        data = client.get("/api/dashboard").get_json()

        assert data["lifetime"]["available"] is False
        # The frontend hides the Money In card on this flag, but still reads the
        # timeline fields — they must exist rather than be missing.
        assert data["timeline"][0]["net_contributions"] == 0
        assert data["timeline"][0]["cumulative_invested"] == 0

    def test_cumulative_invested_accumulates_across_quarters(
        self, client, make_snapshot, make_cash_flows
    ):
        make_snapshot("2025-Q1", "2025-03-31", portfolio=100.0)
        make_snapshot("2025-Q2", "2025-06-30", portfolio=200.0)
        make_snapshot("2025-Q3", "2025-09-30", portfolio=300.0)
        make_cash_flows(
            ("2025-02-01", "deposit", 50.0),
            ("2025-05-01", "deposit", 30.0),
            ("2025-08-01", "deposit", 20.0),
        )

        timeline = client.get("/api/dashboard").get_json()["timeline"]

        assert [t["net_contributions"] for t in timeline] == [50.0, 30.0, 20.0]
        assert [t["cumulative_invested"] for t in timeline] == [50.0, 80.0, 100.0]

    def test_empty_database(self, client):
        data = client.get("/api/dashboard").get_json()
        assert data["timeline"] == []
        assert data["net_worth"] == 0
        assert data["lifetime"]["available"] is False


class TestImportCashflows:
    def test_imports_valid_file(self, client):
        xlsx = _xlsx_bytes(
            VALID_HEADER,
            ("2025-01-15", "Wpłata automatyczna", 1000, "PLN", 1.0, 1000, "Gotówka PLN", "Mat"),
            ("2025-02-20", "Wypłata automatyczna", 300, "PLN", 1.0, 300, "Gotówka PLN", "Mat"),
        )
        resp = client.post("/api/import-cashflows",
                           data={"file": (xlsx, "wklad.xlsx")},
                           content_type="multipart/form-data")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["imported"] == 2
        assert body["net_invested"] == pytest.approx(700.0)

    def test_rejects_reordered_columns(self, client):
        """The parser reads columns by position, so a layout change must fail
        loudly rather than silently importing the wrong numbers."""
        shuffled = ("Operacja", "Data", "Wartość", "Waluta", "Kurs", "Wartość [PLN]", "Konto", "Portfel")
        xlsx = _xlsx_bytes(
            shuffled,
            ("Wpłata automatyczna", "2025-01-15", 1000, "PLN", 1.0, 1000, "Gotówka PLN", "Mat"),
        )
        resp = client.post("/api/import-cashflows",
                           data={"file": (xlsx, "wklad.xlsx")},
                           content_type="multipart/form-data")

        assert resp.status_code == 400
        assert "Unexpected column layout" in resp.get_json()["error"]
        assert db.get_cash_flow_summary()["count"] == 0

    def test_rejects_non_xlsx_extension(self, client):
        resp = client.post("/api/import-cashflows",
                           data={"file": (io.BytesIO(b"not a spreadsheet"), "data.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        assert ".xlsx" in resp.get_json()["error"]

    def test_rejects_missing_file(self, client):
        resp = client.post("/api/import-cashflows", data={},
                           content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_skips_unrecognized_operations(self, client):
        xlsx = _xlsx_bytes(
            VALID_HEADER,
            ("2025-01-15", "Wpłata automatyczna", 1000, "PLN", 1.0, 1000, "Gotówka PLN", "Mat"),
            ("2025-01-16", "Dywidenda", 50, "PLN", 1.0, 50, "Gotówka PLN", "Mat"),
        )
        resp = client.post("/api/import-cashflows",
                           data={"file": (xlsx, "wklad.xlsx")},
                           content_type="multipart/form-data")

        body = resp.get_json()
        assert body["imported"] == 1
        assert body["skipped"] == 1

    def test_failed_import_leaves_existing_data_intact(self, client, make_cash_flows):
        """A rejected file must not wipe the previously imported history."""
        make_cash_flows(("2025-01-01", "deposit", 999.0))

        bad = _xlsx_bytes(("Wrong", "Header"), ("a", "b"))
        resp = client.post("/api/import-cashflows",
                           data={"file": (bad, "wklad.xlsx")},
                           content_type="multipart/form-data")

        assert resp.status_code == 400
        assert db.get_cash_flow_summary()["net_invested"] == pytest.approx(999.0)


class TestUploadLimit:
    def test_oversized_upload_returns_json_not_html(self, client, monkeypatch):
        """Both import endpoints parse the response as JSON, so the 413 must be
        JSON too — otherwise the browser reports a parse error instead of the
        real reason."""
        monkeypatch.setitem(client.application.config, "MAX_CONTENT_LENGTH", 1024)

        resp = client.post("/api/import-cashflows",
                           data={"file": (io.BytesIO(b"x" * 5000), "big.xlsx")},
                           content_type="multipart/form-data")

        assert resp.status_code == 413
        assert resp.is_json
        assert "too large" in resp.get_json()["error"].lower()


class TestAuth:
    PASSWORD = "hasło-testowe-ąćę"

    @pytest.fixture
    def auth_client(self, temp_db, monkeypatch):
        """Test client with password auth enabled."""
        import app as app_module
        monkeypatch.setenv("DASHBOARD_PASSWORD", self.PASSWORD)
        monkeypatch.setenv("SECRET_KEY", "test-secret")
        app_module.app.config["TESTING"] = True
        app_module.app.config["SECRET_KEY"] = "test-secret"
        return app_module.app.test_client()

    def test_non_ascii_password_is_accepted(self, auth_client):
        """hmac.compare_digest rejects non-ASCII str, so both sides are encoded
        to UTF-8 first. A Polish character in the password would otherwise
        raise TypeError and make login impossible."""
        resp = auth_client.post("/login", data={"password": self.PASSWORD})
        assert resp.status_code == 302

    def test_wrong_password_rejected(self, auth_client):
        resp = auth_client.post("/login", data={"password": "wrong"})
        assert resp.status_code == 401

    def test_empty_password_rejected(self, auth_client):
        resp = auth_client.post("/login", data={"password": ""})
        assert resp.status_code == 401

    def test_protected_routes_redirect_when_unauthenticated(self, auth_client):
        for path in ("/", "/compare", "/forecast", "/retirement", "/import",
                     "/api/dashboard", "/api/snapshots"):
            resp = auth_client.get(path)
            assert resp.status_code == 302, path
            assert "/login" in resp.headers["Location"], path

    def test_login_page_and_static_stay_reachable(self, auth_client):
        assert auth_client.get("/login").status_code == 200

    def test_authenticated_session_can_read_the_api(self, auth_client):
        auth_client.post("/login", data={"password": self.PASSWORD})
        assert auth_client.get("/api/dashboard").status_code == 200

    def test_logout_revokes_access(self, auth_client):
        auth_client.post("/login", data={"password": self.PASSWORD})
        auth_client.post("/logout")
        assert auth_client.get("/api/dashboard").status_code == 302

    def test_auth_disabled_when_password_unset(self, client):
        """Dev mode: no DASHBOARD_PASSWORD means the site is open."""
        assert client.get("/api/dashboard").status_code == 200


class TestImportCsvGuards:
    def test_rejects_filename_without_a_date(self, client):
        resp = client.post("/api/import-csv",
                           data={"file": (io.BytesIO(b"Walor;\n"), "portfolio.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "date" in resp.get_json()["error"].lower()

    def test_rejects_duplicate_snapshot_date(self, client, make_snapshot):
        make_snapshot("2026-Q1", "2026-03-31", portfolio=100.0)
        resp = client.post("/api/import-csv",
                           data={"file": (io.BytesIO(b"Walor;\n"), "export_2026-03-31.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 409
        assert "already imported" in resp.get_json()["error"]


class TestPpkIntegration:
    """PPK is entered manually but counts as invested capital, so it is
    injected as a synthetic position. Every consumer of position lists must
    use that injection, or views disagree on the portfolio total."""

    @pytest.fixture
    def snapshot_with_ppk(self, temp_db):
        sid = db.create_snapshot("2026-Q1", "2026-03-31")
        db.insert_positions(sid, [{
            "name": "Fund", "ticker": "F", "isin": None, "account": "Interactive Brokers",
            "group_name": "g", "currency": "PLN", "tags": "ETF", "value_pln": 100_000.0}])
        db.save_manual_entries(sid, [
            {"type": "cash", "label": "Cash", "currency": "PLN",
             "original_amount": 10_000.0, "amount_pln": 10_000.0},
            {"type": "ppk", "label": "PPK", "currency": "PLN",
             "original_amount": 25_000.0, "amount_pln": 25_000.0},
        ])
        return sid

    def test_ppk_is_included_in_the_portfolio_total_once(self, client, snapshot_with_ppk):
        d = client.get("/api/dashboard").get_json()
        assert d["ppk_total"] == pytest.approx(25_000)
        assert d["portfolio_total"] == pytest.approx(125_000)
        # Counted once: portfolio (incl. PPK) + cash, no mortgage.
        assert d["net_worth"] == pytest.approx(135_000)

    def test_ppk_appears_as_its_own_tag_and_account(self, client, snapshot_with_ppk):
        d = client.get("/api/dashboard").get_json()
        assert d["by_tags"]["PPK"] == pytest.approx(25_000)
        assert d["by_account"]["PPK"] == pytest.approx(25_000)

    def test_timeline_portfolio_total_includes_ppk(self, client, snapshot_with_ppk):
        d = client.get("/api/dashboard").get_json()
        row = d["timeline"][-1]
        assert row["ppk_total"] == pytest.approx(25_000)
        assert row["portfolio_total"] == pytest.approx(125_000)
        assert row["net_worth"] == pytest.approx(135_000)

    def test_dashboard_and_compare_agree(self, client, snapshot_with_ppk):
        """Regression: /api/compare read positions straight from the DB and
        skipped the injection, so it reported a lower portfolio than the
        dashboard for the same quarter."""
        sid2 = db.create_snapshot("2025-Q4", "2025-12-31")
        db.insert_positions(sid2, [{
            "name": "Fund", "ticker": "F", "isin": None, "account": "Interactive Brokers",
            "group_name": "g", "currency": "PLN", "tags": "ETF", "value_pln": 90_000.0}])

        d = client.get("/api/dashboard").get_json()
        c = client.get("/api/compare").get_json()
        assert c["totals"]["portfolio"][1] == pytest.approx(d["portfolio_total"])
        assert c["totals"]["net_worth"][1] == pytest.approx(d["net_worth"])
        assert any(p["account"] == "PPK" for p in c["positions_b"])

    def test_no_ppk_entry_means_no_ppk_anywhere(self, client, make_snapshot):
        make_snapshot("2026-Q1", "2026-03-31", portfolio=100_000.0, cash=5_000.0)
        d = client.get("/api/dashboard").get_json()
        assert d["ppk_total"] == 0
        assert "PPK" not in d["by_tags"]
        assert d["portfolio_total"] == pytest.approx(100_000)

    def test_ppk_is_not_counted_as_market_gains(self, client, snapshot_with_ppk, make_cash_flows):
        """Regression: PPK contributions come from payroll and never appear in
        the myfund cash-flow export. Folding PPK into portfolio value therefore
        raised current_wealth without raising net_invested, reporting the whole
        PPK balance as market gains."""
        make_cash_flows(("2026-01-01", "deposit", 80_000.0))

        lifetime = client.get("/api/dashboard").get_json()["lifetime"]

        # Tracked capital is 100k positions + 10k cash; PPK's 25k is excluded
        # from both sides, so gains are 110k - 80k, not 135k - 80k.
        assert lifetime["current_wealth"] == pytest.approx(110_000)
        assert lifetime["market_gains"] == pytest.approx(30_000)

    def test_ppk_still_counts_toward_net_worth(self, client, snapshot_with_ppk, make_cash_flows):
        """Excluding PPK from the gains calculation must not remove it from
        net worth — it is still real money."""
        make_cash_flows(("2026-01-01", "deposit", 80_000.0))
        d = client.get("/api/dashboard").get_json()
        assert d["net_worth"] == pytest.approx(135_000)


class TestImportPage:
    """The import UI moved off the dashboard onto its own tab."""

    def test_import_page_renders(self, client):
        resp = client.get("/import")
        assert resp.status_code == 200
        assert b"import.js" in resp.data

    def test_every_page_links_to_the_import_tab(self, client):
        for path in ("/", "/compare", "/forecast", "/retirement", "/import"):
            assert b'href="/import"' in client.get(path).data, path

    def test_dashboard_no_longer_carries_the_import_forms(self, client):
        """Two pages owning the same element ids would make getElementById
        ambiguous and let a stale copy silently win."""
        body = client.get("/").data
        for element_id in (b'id="csvFileInput"', b'id="cashflowsFileInput"',
                           b'id="entryQuarter"', b'id="cashEntries"'):
            assert element_id not in body, element_id

    def test_snapshots_endpoint_lists_quarters(self, client, make_snapshot):
        make_snapshot("2025-Q4", "2025-12-31", portfolio=100_000.0)
        make_snapshot("2026-Q1", "2026-03-31", portfolio=120_000.0)
        rows = client.get("/api/snapshots").get_json()
        assert [r["quarter"] for r in rows] == ["2026-Q1", "2025-Q4"]
        assert set(rows[0]) == {"id", "quarter", "snapshot_date"}

    def test_snapshots_endpoint_is_empty_before_any_import(self, client):
        assert client.get("/api/snapshots").get_json() == []


class TestEmptyStates:
    """A database with no snapshots must explain itself, not render a dead page.

    Compare used to parse its own 404 as data and then map over an undefined
    quarters list, leaving empty selectors and no message at all.
    """

    def test_compare_api_reports_why_it_is_empty(self, client):
        resp = client.get("/api/compare")
        assert resp.status_code == 404
        assert "error" in resp.get_json()

    def test_pages_still_render_with_no_data(self, client):
        for path in ("/", "/compare", "/forecast", "/retirement", "/import"):
            assert client.get(path).status_code == 200, path
