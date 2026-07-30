"""Tests for the myFund CSV parsing helpers in import_data.py."""

import pytest

import import_data


class TestExtractDateFromFilename:
    def test_extracts_iso_date(self):
        assert import_data.extract_date_from_filename(
            "myfund.pl_Mat_portfelSklad_2026-04-01.csv"
        ) == "2026-04-01"

    def test_returns_none_without_a_date(self):
        assert import_data.extract_date_from_filename("portfolio.csv") is None

    def test_ignores_partial_dates(self):
        assert import_data.extract_date_from_filename("export_2026-04.csv") is None

    def test_takes_the_first_date_when_several_present(self):
        assert import_data.extract_date_from_filename(
            "2025-01-01_to_2026-04-01.csv"
        ) == "2025-01-01"


class TestDateToQuarter:
    @pytest.mark.parametrize("date_str,expected", [
        ("2025-01-01", "2025-Q1"),
        ("2025-03-31", "2025-Q1"),   # last day of Q1
        ("2025-04-01", "2025-Q2"),   # first day of Q2
        ("2025-06-30", "2025-Q2"),
        ("2025-07-01", "2025-Q3"),
        ("2025-09-30", "2025-Q3"),
        ("2025-10-01", "2025-Q4"),
        ("2025-12-31", "2025-Q4"),   # last day of the year
    ])
    def test_quarter_boundaries(self, date_str, expected):
        assert import_data.date_to_quarter(date_str) == expected


class TestParseNumber:
    @pytest.mark.parametrize("raw,expected", [
        ("1048009.78", 1048009.78),
        ("1 048 009.78", 1048009.78),        # regular spaces as separators
        ("1\xa0048\xa0009.78", 1048009.78),  # non-breaking spaces (what myFund emits)
        ("-3 281.83", -3281.83),
        ("0", 0.0),
    ])
    def test_parses_separators_and_signs(self, raw, expected):
        assert import_data.parse_number(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["", "   ", None, "not a number"])
    def test_falls_back_to_zero_on_junk(self, raw):
        assert import_data.parse_number(raw) == 0.0


class TestExtractTicker:
    def test_stock_ticker_from_first_parens(self):
        assert import_data.extract_ticker(
            "NVIDIA Corporation (NVDA) (Interactive Brokers)", "Interactive Brokers"
        ) == "NVDA"

    def test_bond_falls_back_to_first_word_on_date_parens(self):
        assert import_data.extract_ticker(
            "EDO1031 (2021-10-22) (Obligacje Skarbowe) (3.90%)", "Obligacje Skarbowe"
        ) == "EDO1031"

    def test_bond_falls_back_to_first_word_on_percent_parens(self):
        assert import_data.extract_ticker("ROD0931 (7.20%)", "Obligacje Skarbowe") == "ROD0931"

    def test_account_name_in_parens_is_not_a_ticker(self):
        assert import_data.extract_ticker("XTB (BOSSA IKE)", "BOSSA IKE") == "XTB"

    def test_short_ticker_not_swallowed_by_account_match(self):
        # Regression guard for the documented 3-char threshold: ticker "V" must
        # survive even though "V" appears inside "Interactive Brokers".
        assert import_data.extract_ticker(
            "Visa Inc. (V) (Interactive Brokers)", "Interactive Brokers"
        ) == "V"

    def test_no_parens_uses_first_word(self):
        assert import_data.extract_ticker("Gotowka PLN", "Cash") == "Gotowka"


class TestCleanGroupName:
    def test_replaces_non_breaking_space(self):
        assert import_data.clean_group_name("Akcje\xa0GPW") == "Akcje GPW"

    def test_passes_through_none(self):
        assert import_data.clean_group_name(None) is None


class TestParseCsv:
    """parse_csv reads a cp1250-encoded, semicolon-delimited myFund export."""

    HEADER = (
        "Walor;ISIN;Konto;Grupa;Waluta waloru;Tagi;Wartość waloru [PLN]"
    )

    def _write_csv(self, tmp_path, *rows):
        path = tmp_path / "myfund.pl_test_2026-03-31.csv"
        path.write_text("\n".join((self.HEADER,) + rows), encoding="cp1250")
        return str(path)

    def test_parses_a_basic_position(self, tmp_path):
        path = self._write_csv(
            tmp_path,
            "NVIDIA Corporation (NVDA);US67066G1040;Interactive Brokers;Akcje US;USD;Akcje US;100 000.50",
        )
        positions = import_data.parse_csv(path)
        assert len(positions) == 1
        assert positions[0] == {
            "name": "NVIDIA Corporation (NVDA)",
            "ticker": "NVDA",
            "isin": "US67066G1040",
            "account": "Interactive Brokers",
            "group_name": "Akcje US",
            "currency": "USD",
            "tags": "Akcje US",
            "value_pln": pytest.approx(100000.50),
        }

    def test_skips_totals_row(self, tmp_path):
        path = self._write_csv(
            tmp_path,
            "Real Position (RP);;Acct;Grp;PLN;Tag;500",
            "Razem;;;;;;500",
        )
        positions = import_data.parse_csv(path)
        assert [p["name"] for p in positions] == ["Real Position (RP)"]

    def test_skips_zero_value_and_blank_rows(self, tmp_path):
        path = self._write_csv(
            tmp_path,
            "Kept (KEPT);;Acct;Grp;PLN;Tag;100",
            "Zero Value (ZERO);;Acct;Grp;PLN;Tag;0",
            ";;;;;;",
        )
        positions = import_data.parse_csv(path)
        assert [p["ticker"] for p in positions] == ["KEPT"]

    def test_decodes_html_entities(self, tmp_path):
        # &gt; contains a literal semicolon, which would otherwise split the row
        # into extra columns and corrupt every field after it.
        path = self._write_csv(
            tmp_path,
            "Fund A &gt; B (FUND);;Acct;Grp;PLN;Tag;250",
        )
        positions = import_data.parse_csv(path)
        assert len(positions) == 1
        assert positions[0]["name"] == "Fund A > B (FUND)"
        assert positions[0]["value_pln"] == pytest.approx(250)

    def test_empty_optional_fields_become_none(self, tmp_path):
        path = self._write_csv(tmp_path, "Bare (BARE);;;;;;42")
        position = import_data.parse_csv(path)[0]
        assert position["isin"] is None
        assert position["account"] is None
        assert position["group_name"] is None
        assert position["tags"] is None
