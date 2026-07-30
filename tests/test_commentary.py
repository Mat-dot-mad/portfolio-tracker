"""Tests for the LLM quarterly commentary.

The most important test here is the privacy invariant: this is the only feature
that sends portfolio data off the machine, and the agreed boundary is
percentages and position names but no absolute amounts and no account names.
That is enforced rather than documented.
"""

import json
import re

import pytest

import app as app_module
import db
import gemini


@pytest.fixture
def two_quarters(temp_db, make_snapshot, make_cash_flows):
    """Two quarters with deliberately distinctive amounts and account names,
    so a leak into the outbound payload is unmistakable."""
    a = db.create_snapshot("2025-Q4", "2025-12-31")
    db.insert_positions(a, [
        {"name": "iShares Core MSCI World UCITS ETF (Acc) (IWDA.AS) (BOSSA IKZE)",
         "ticker": "IWDA.AS", "isin": None, "account": "BOSSA IKZE",
         "group_name": "ETF", "currency": "PLN", "tags": "ETF", "value_pln": 400000.0},
        {"name": "NVIDIA Corporation (NVDA) (Interactive Brokers)",
         "ticker": "NVDA", "isin": None, "account": "Interactive Brokers",
         "group_name": "Akcje US", "currency": "USD", "tags": "Akcje US", "value_pln": 600000.0},
    ])
    db.save_manual_entries(a, [
        {"type": "cash", "label": "Cash", "currency": "PLN",
         "original_amount": 88888.0, "amount_pln": 88888.0},
    ])

    b = db.create_snapshot("2026-Q1", "2026-03-31")
    db.insert_positions(b, [
        {"name": "iShares Core MSCI World UCITS ETF (Acc) (IWDA.AS) (BOSSA IKZE)",
         "ticker": "IWDA.AS", "isin": None, "account": "BOSSA IKZE",
         "group_name": "ETF", "currency": "PLN", "tags": "ETF", "value_pln": 500000.0},
        {"name": "NVIDIA Corporation (NVDA) (Interactive Brokers)",
         "ticker": "NVDA", "isin": None, "account": "Interactive Brokers",
         "group_name": "Akcje US", "currency": "USD", "tags": "Akcje US", "value_pln": 561053.0},
    ])
    db.save_manual_entries(b, [
        {"type": "cash", "label": "Cash", "currency": "PLN",
         "original_amount": 77777.0, "amount_pln": 77777.0},
    ])
    make_cash_flows(("2026-02-01", "deposit", 45000.0))
    return a, b


def _payload(client):
    data = app_module._build_dashboard_data()
    return app_module._build_commentary_payload(data)


class TestPrivacyInvariant:
    """The agreed boundary: relative figures and position names only."""

    def test_no_absolute_amounts_reach_the_payload(self, client, two_quarters):
        serialised = json.dumps(_payload(client), ensure_ascii=False)

        # Every distinctive absolute figure from the fixture.
        for amount in ("400000", "600000", "500000", "561053", "88888", "77777", "45000"):
            assert amount not in serialised, f"absolute amount {amount} leaked"

    def test_no_account_names_reach_the_payload(self, client, two_quarters):
        serialised = json.dumps(_payload(client), ensure_ascii=False)
        for account in ("BOSSA IKZE", "Interactive Brokers"):
            assert account not in serialised, f"account name {account!r} leaked"

    def test_position_names_are_kept(self, client, two_quarters):
        """Names are allowed — they are what make the commentary specific."""
        serialised = json.dumps(_payload(client), ensure_ascii=False)
        assert "NVIDIA" in serialised
        assert "iShares Core MSCI World" in serialised

    def test_every_numeric_value_is_a_plausible_percentage(self, client, two_quarters):
        """Belt-and-braces against a future field leaking a PLN figure: no
        number in the payload should be anywhere near portfolio scale."""
        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield from walk(v)
            elif isinstance(node, list):
                for v in node:
                    yield from walk(v)
            elif isinstance(node, (int, float)) and not isinstance(node, bool):
                yield node

        for value in walk(_payload(client)):
            assert abs(value) < 10000, f"{value} is too large to be a percentage"


class TestStripAccount:
    @pytest.mark.parametrize("name,account,expected", [
        ("iShares Core MSCI World UCITS ETF (Acc) (IWDA.AS) (BOSSA IKZE)",
         "BOSSA IKZE", "iShares Core MSCI World UCITS ETF (Acc) (IWDA.AS)"),
        ("NVIDIA Corporation (NVDA) (Interactive Brokers)",
         "Interactive Brokers", "NVIDIA Corporation (NVDA)"),
        ("ASSECOPOL (ACP) (XTB (PLN))", "XTB (PLN)", "ASSECOPOL (ACP)"),
        ("EDO1031 (2021-10-22) (Obligacje Skarbowe)",
         "Obligacje Skarbowe", "EDO1031 (2021-10-22)"),
    ])
    def test_removes_account_suffix(self, name, account, expected):
        assert app_module._strip_account(name, account) == expected

    def test_leaves_ticker_and_share_class_intact(self):
        out = app_module._strip_account(
            "iShares Core MSCI World UCITS ETF (Acc) (IWDA.AS) (BOSSA IKZE)", "BOSSA IKZE")
        assert "IWDA.AS" in out and "(Acc)" in out


class TestPayloadContent:
    def test_reports_relative_changes(self, client, two_quarters):
        p = _payload(client)
        assert p["quarter"] == "2026-Q1"
        assert p["previous_quarter"] == "2025-Q4"
        # portfolio 1,000,000 -> 1,061,053
        assert p["portfolio_change_pct"] == pytest.approx(6.1, abs=0.1)

    def test_allocation_deltas(self, client, two_quarters):
        p = _payload(client)
        # ETF 40% -> 47.1%, Akcje US 60% -> 52.9%
        assert p["allocation_pct_by_tag"]["ETF"] == pytest.approx(47.1, abs=0.2)
        assert p["allocation_change_pp_by_tag"]["ETF"] > 0
        assert p["allocation_change_pp_by_tag"]["Akcje US"] < 0

    def test_market_return_excludes_contributions(self, client, two_quarters):
        p = _payload(client)
        # The 45k deposit must not be counted as market performance, so the
        # market-only figure is below the raw net-worth change.
        assert p["market_return_pct_excluding_contributions"] < p["net_worth_change_pct"]

    def test_position_change_field_is_named_as_a_value_change(self, client, two_quarters):
        """Regression: the field was called 'change_pct', so the model reported a
        position that had been topped up as though the security had appreciated
        ('Microsoft saw a particularly large gain of 103.0 percent'). The name
        and the accompanying note have to make the distinction explicit."""
        p = _payload(client)
        for position in p["notable_positions"]:
            assert "value_change_pct" in position
            assert "change_pct" not in position

    def test_payload_warns_that_position_changes_include_trading(self, client, two_quarters):
        notes = " ".join(_payload(client)["notes"]).lower()
        assert "value_change_pct" in notes
        assert "buying" in notes or "selling" in notes

    def test_returns_none_with_insufficient_history(self, client, make_snapshot):
        make_snapshot("2026-Q1", "2026-03-31", portfolio=1000.0)
        assert app_module._build_commentary_payload(app_module._build_dashboard_data()) is None


class TestPayloadHash:
    def test_hash_is_independent_of_key_order(self):
        """Regression: the hash was built from json.dumps() without sort_keys,
        and allocation keys came from iterating a set. String hash
        randomisation meant the order differed between processes, so cached
        commentary looked permanently stale to the server that served it."""
        a = {"alpha": 1, "beta": {"x": 1, "y": 2}, "gamma": [1, 2]}
        b = {"gamma": [1, 2], "beta": {"y": 2, "x": 1}, "alpha": 1}
        assert app_module._payload_hash(a) == app_module._payload_hash(b)

    def test_hash_changes_when_a_value_changes(self):
        a = {"alpha": 1}
        b = {"alpha": 2}
        assert app_module._payload_hash(a) != app_module._payload_hash(b)

    def test_allocation_keys_are_emitted_in_sorted_order(self, client, two_quarters):
        keys = list(_payload(client)["allocation_change_pp_by_tag"].keys())
        assert keys == sorted(keys)


class TestCommentaryEndpoints:
    def test_unavailable_without_an_api_key(self, client, two_quarters, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        body = client.get("/api/commentary").get_json()
        assert body["available"] is False

    def test_post_refused_without_an_api_key(self, client, two_quarters, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert client.post("/api/commentary").status_code == 503

    def test_generate_then_serve_from_cache(self, client, two_quarters, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_MODEL", "test-model")

        calls = []

        def fake_generate(payload_json):
            calls.append(payload_json)
            return "The quarter was uneventful."

        monkeypatch.setattr(gemini, "generate_commentary", fake_generate)

        post = client.post("/api/commentary").get_json()
        assert post["text"] == "The quarter was uneventful."
        assert post["model"] == "test-model"
        assert len(calls) == 1

        # A subsequent GET must serve the cache without calling the API again.
        get = client.get("/api/commentary").get_json()
        assert get["text"] == "The quarter was uneventful."
        assert get["stale"] is False
        assert len(calls) == 1

    def test_api_failure_surfaces_as_502(self, client, two_quarters, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini, "generate_commentary",
                            lambda _p: (_ for _ in ()).throw(ValueError("quota reached")))
        resp = client.post("/api/commentary")
        assert resp.status_code == 502
        assert "quota reached" in resp.get_json()["error"]

    def test_cache_marked_stale_when_figures_change(self, client, two_quarters, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini, "generate_commentary", lambda _p: "text")
        client.post("/api/commentary")
        assert client.get("/api/commentary").get_json()["stale"] is False

        # Change the underlying data; the cached text no longer matches.
        _a, b = two_quarters
        db.save_manual_entries(b, [{"type": "cash", "label": "Cash", "currency": "PLN",
                                    "original_amount": 1.0, "amount_pln": 1.0}])
        assert client.get("/api/commentary").get_json()["stale"] is True

    def test_deleting_a_snapshot_removes_its_commentary(self, client, two_quarters, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini, "generate_commentary", lambda _p: "text")
        client.post("/api/commentary")
        _a, b = two_quarters
        assert db.get_commentary(b) is not None
        db.delete_snapshot(b)
        assert db.get_commentary(b) is None


class TestGeminiClient:
    def test_is_configured_follows_the_env_var(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert gemini.is_configured() is False
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        assert gemini.is_configured() is True

    def test_generate_without_a_key_raises(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            gemini.generate_commentary("{}")

    @pytest.mark.parametrize("status,fragment", [
        (429, "quota"),
        (401, "API key"),
        (404, "not found"),
        (500, "500"),
    ])
    def test_http_errors_become_readable_messages(self, monkeypatch, status, fragment):
        class FakeResponse:
            status_code = status
            text = "error body"
            def json(self): return {}

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: FakeResponse())
        with pytest.raises(ValueError, match=re.escape(fragment)):
            gemini.generate_commentary("{}")

    def test_blocked_response_raises(self, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self): return {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: FakeResponse())
        with pytest.raises(ValueError, match="SAFETY"):
            gemini.generate_commentary("{}")

    def test_truncated_response_raises_instead_of_returning_half_a_sentence(self, monkeypatch):
        """Regression: with a thinking-capable model, internal reasoning consumed
        the output budget and the review was cached cut off mid-sentence
        ('...net worth rose by 21.'). Truncation must fail, not be presented as
        a finished review."""
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"candidates": [{
                    "content": {"parts": [{"text": "The portfolio grew by 21.3%, while net worth rose by 21."}]},
                    "finishReason": "MAX_TOKENS",
                }]}

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: FakeResponse())
        with pytest.raises(ValueError, match="token output limit"):
            gemini.generate_commentary("{}")

    def test_thinking_parts_are_excluded_from_the_text(self, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"candidates": [{
                    "content": {"parts": [
                        {"text": "internal reasoning", "thought": True},
                        {"text": "The visible review."},
                    ]},
                    "finishReason": "STOP",
                }]}

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: FakeResponse())
        assert gemini.generate_commentary("{}") == "The visible review."

    def test_all_budget_spent_on_thinking_reports_the_reason(self, monkeypatch):
        """A thinking model can burn the whole budget and return no text at all."""
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: FakeResponse())
        with pytest.raises(ValueError, match="token output limit"):
            gemini.generate_commentary("{}")

    def test_output_budget_is_large_enough_for_a_thinking_model(self):
        """Guards against the cap being trimmed back to a value that only suits
        non-thinking models."""
        assert gemini.MAX_OUTPUT_TOKENS >= 2000

    def test_successful_response_is_joined(self, monkeypatch):
        class FakeResponse:
            status_code = 200
            text = ""
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "Hello "}, {"text": "world"}]}}]}

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: FakeResponse())
        assert gemini.generate_commentary("{}") == "Hello world"
