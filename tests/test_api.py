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
        for path in ("/", "/compare", "/forecast", "/api/dashboard"):
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
