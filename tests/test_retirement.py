"""Tests for the retirement simulation engine.

Deterministic where possible: passing returns=[0.0] removes market randomness
so the tax and age-gating arithmetic can be asserted exactly.

The behaviour these exist to protect is age gating. A plan can look fully
funded on total net worth and still fail, because IKE and IKZE money cannot be
spent at 55. Getting that wrong produces a confidently wrong retirement date,
which is the worst possible failure mode for this feature.
"""

import random

import pytest

import retirement
from retirement import IKE, IKZE, PPK, TAXABLE, Bucket


def make_params(**overrides):
    """Baseline params: everything off unless a test switches it on."""
    base = {
        "current_age": 40,
        "retirement_age": 65,
        "horizon_age": 90,
        "annual_spending": 100_000.0,
        "annual_savings": 0.0,
        # Starting balances
        "start_taxable": 0.0, "start_taxable_basis": 0.0,
        "start_ike": 0.0, "start_ike_basis": 0.0,
        "start_ikze": 0.0, "start_ikze_basis": 0.0,
        "start_ppk": 0.0,
        # Polish rules — parameters, never constants
        "ike_access_age": 60,
        "ikze_access_age": 65,
        "belka_rate": 0.19,
        "ikze_withdrawal_rate": 0.10,
        "ike_annual_limit": 26_019.0,
        "ikze_annual_limit": 10_407.0,
        # ZUS
        "zus_annual": 0.0,
        "zus_start_age": 65,
        # PPK
        "ppk_enabled": False,
        "ppk_gross_salary": 0.0,
        "ppk_employee_rate": 0.02,
        "ppk_employer_rate": 0.015,
        "ppk_state_annual": 240.0,
        "ppk_access_age": 60,
        "ppk_lump_sum_fraction": 0.25,
        "ppk_installment_years": 10,
    }
    base.update(overrides)
    return base


ZERO_RETURN = [0.0]


class TestBucket:
    def test_contributions_count_as_basis_not_gain(self):
        b = Bucket(100, 100)
        b.add(50)
        assert b.value == 150 and b.basis == 150
        assert b.gain_fraction() == 0

    def test_growth_creates_gain(self):
        b = Bucket(100, 100)
        b.grow(0.5)
        assert b.value == 150
        assert b.gain_fraction() == pytest.approx(1 / 3)

    def test_withdrawal_reduces_basis_proportionally(self):
        b = Bucket(200, 100)          # half gain
        b.take(100)
        assert b.value == 100
        assert b.basis == pytest.approx(50)
        assert b.gain_fraction() == pytest.approx(0.5)   # ratio preserved

    def test_cannot_take_more_than_present(self):
        b = Bucket(50, 50)
        assert b.take(100) == 50
        assert b.value == 0


class TestTaxTreatment:
    def test_belka_applies_only_to_the_gain_portion(self):
        params = make_params()
        bucket = Bucket(200, 100)     # 50% gain
        net = retirement._net_of_tax(100, TAXABLE, bucket, params, age=70)
        # 19% on half of the withdrawal
        assert net == pytest.approx(100 * (1 - 0.5 * 0.19))

    def test_pure_basis_withdrawal_is_untaxed(self):
        params = make_params()
        assert retirement._net_of_tax(100, TAXABLE, Bucket(100, 100), params, 70) == 100

    def test_ike_is_tax_free_after_the_access_age(self):
        params = make_params()
        bucket = Bucket(200, 100)
        assert retirement._net_of_tax(100, IKE, bucket, params, age=60) == 100

    def test_ikze_taxes_the_whole_withdrawal_not_just_gains(self):
        params = make_params()
        bucket = Bucket(200, 200)     # no gain at all
        net = retirement._net_of_tax(100, IKZE, bucket, params, age=65)
        assert net == pytest.approx(90)      # flat 10% regardless

    def test_gross_up_inverts_the_tax(self):
        params = make_params()
        bucket = Bucket(200, 100)
        gross = retirement._gross_for_net(100, TAXABLE, bucket, params, 70)
        assert retirement._net_of_tax(gross, TAXABLE, bucket, params, 70) == pytest.approx(100)


class TestAgeGating:
    def test_wrappers_are_locked_before_their_access_age(self):
        p = make_params()
        assert retirement._is_accessible(TAXABLE, p, 40) is True
        assert retirement._is_accessible(IKE, p, 59) is False
        assert retirement._is_accessible(IKE, p, 60) is True
        assert retirement._is_accessible(IKZE, p, 64) is False
        assert retirement._is_accessible(IKZE, p, 65) is True

    def test_rich_but_locked_plan_fails_the_bridge(self):
        """The behaviour this whole module exists for.

        Two million in IKE and nothing accessible: net worth says comfortable,
        reality says you cannot buy groceries at 50.
        """
        params = make_params(
            current_age=50, retirement_age=50,
            start_taxable=0.0, start_ike=2_000_000.0, start_ike_basis=2_000_000.0,
            annual_spending=100_000.0,
        )
        ok, records = retirement.simulate_path(params, ZERO_RETURN, random.Random(1))
        assert ok is False
        assert records[0]["shortfall"] > 0
        assert records[0]["ike"] == 2_000_000.0    # untouched, because unreachable

    def test_same_wealth_in_taxable_succeeds(self):
        """Identical total, different wrapper — now it works. Isolates gating
        as the cause rather than insufficient money."""
        params = make_params(
            current_age=50, retirement_age=50,
            start_taxable=2_000_000.0, start_taxable_basis=2_000_000.0,
            annual_spending=100_000.0, horizon_age=60,
        )
        ok, _ = retirement.simulate_path(params, ZERO_RETURN, random.Random(1))
        assert ok is True

    def test_bridge_survives_until_ike_unlocks(self):
        """Enough taxable to reach 60, then IKE carries the rest."""
        params = make_params(
            current_age=55, retirement_age=55, horizon_age=70,
            start_taxable=550_000.0, start_taxable_basis=550_000.0,
            start_ike=1_000_000.0, start_ike_basis=1_000_000.0,
            annual_spending=100_000.0,
        )
        ok, records = retirement.simulate_path(params, ZERO_RETURN, random.Random(1))
        assert ok is True
        at_60 = next(r for r in records if r["age"] == 60)
        assert at_60["taxable"] < 100_000      # bridge nearly exhausted
        at_65 = next(r for r in records if r["age"] == 65)
        assert at_65["ike"] < 1_000_000        # IKE now being drawn


class TestContributionSplit:
    def test_wrappers_fill_before_taxable(self):
        params = make_params(annual_savings=50_000.0, current_age=40)
        split = retirement._annual_contributions(params, 40)
        assert split[IKZE] == params["ikze_annual_limit"]
        assert split[IKE] == params["ike_annual_limit"]
        assert split[TAXABLE] == pytest.approx(
            50_000 - params["ikze_annual_limit"] - params["ike_annual_limit"])

    def test_small_savings_never_exceed_the_ikze_limit(self):
        params = make_params(annual_savings=5_000.0)
        split = retirement._annual_contributions(params, 40)
        assert split[IKZE] == 5_000.0
        assert split[IKE] == 0 and split[TAXABLE] == 0

    def test_contributions_stop_at_retirement(self):
        params = make_params(annual_savings=50_000.0, retirement_age=60)
        assert sum(retirement._annual_contributions(params, 60).values()) == 0

    def test_ppk_comes_from_salary_not_savings(self):
        params = make_params(
            annual_savings=0.0, ppk_enabled=True, ppk_gross_salary=200_000.0,
            ppk_employee_rate=0.02, ppk_employer_rate=0.015, ppk_state_annual=240.0)
        split = retirement._annual_contributions(params, 40)
        assert split[PPK] == pytest.approx(200_000 * 0.035 + 240)
        assert split[TAXABLE] == 0


class TestIncomeSources:
    def test_zus_reduces_the_draw_on_capital(self):
        with_zus = make_params(
            current_age=65, retirement_age=65, horizon_age=75,
            start_taxable=400_000.0, start_taxable_basis=400_000.0,
            annual_spending=100_000.0, zus_annual=60_000.0, zus_start_age=65)
        without = dict(with_zus, zus_annual=0.0)

        ok_with, _ = retirement.simulate_path(with_zus, ZERO_RETURN, random.Random(1))
        ok_without, _ = retirement.simulate_path(without, ZERO_RETURN, random.Random(1))
        assert ok_with is True
        assert ok_without is False      # same capital, fails without the pension

    def test_zus_does_not_pay_before_its_start_age(self):
        params = make_params(
            current_age=60, retirement_age=60, horizon_age=70,
            start_taxable=150_000.0, start_taxable_basis=150_000.0,
            annual_spending=100_000.0, zus_annual=100_000.0, zus_start_age=65)
        ok, _ = retirement.simulate_path(params, ZERO_RETURN, random.Random(1))
        assert ok is False              # runs dry bridging 60-65

    def test_ppk_pays_a_lump_sum_then_installments(self):
        params = make_params(
            current_age=60, retirement_age=60, horizon_age=72,
            start_taxable=10_000.0, start_taxable_basis=10_000.0,
            start_ppk=500_000.0, annual_spending=40_000.0,
            ppk_access_age=60, ppk_lump_sum_fraction=0.25, ppk_installment_years=10)
        ok, records = retirement.simulate_path(params, ZERO_RETURN, random.Random(1))
        first = records[0]

        # Year one does two things: 25% comes out as the lump sum, and the
        # first of ten installments is paid from what remains.
        after_lump = 500_000 * 0.75
        first_installment = after_lump / 10
        assert first["ppk"] == pytest.approx(after_lump - first_installment, rel=0.01)

        # The lump sum lands in the taxable pot rather than vanishing.
        assert first["taxable"] > 10_000
        assert ok is True

    def test_ppk_pot_is_exhausted_over_the_installment_period(self):
        params = make_params(
            current_age=60, retirement_age=60, horizon_age=75,
            start_taxable=2_000_000.0, start_taxable_basis=2_000_000.0,
            start_ppk=500_000.0, annual_spending=40_000.0,
            ppk_access_age=60, ppk_installment_years=10)
        _ok, records = retirement.simulate_path(params, ZERO_RETURN, random.Random(1))
        at_70 = next(r for r in records if r["age"] == 70)
        assert at_70["ppk"] == pytest.approx(0, abs=1.0)


class TestSolvers:
    def test_earliest_age_is_later_when_spending_is_higher(self):
        base = make_params(
            current_age=45, annual_savings=60_000.0, horizon_age=85,
            start_taxable=500_000.0, start_taxable_basis=500_000.0,
            start_ike=300_000.0, start_ike_basis=300_000.0,
            annual_spending=80_000.0)
        cheap, _ = retirement.earliest_feasible_age(base, ZERO_RETURN, paths=40, seed=1)
        dear, _ = retirement.earliest_feasible_age(
            dict(base, annual_spending=200_000.0), ZERO_RETURN, paths=40, seed=1)
        assert cheap is not None
        assert dear is None or dear > cheap

    def test_unreachable_target_returns_none(self):
        params = make_params(
            current_age=60, annual_savings=0.0,
            start_taxable=1_000.0, start_taxable_basis=1_000.0,
            annual_spending=500_000.0)
        age, rate = retirement.earliest_feasible_age(params, ZERO_RETURN, paths=20, seed=1)
        assert age is None
        assert rate < 0.9

    def test_sustainable_spending_is_self_consistent(self):
        """Whatever the solver returns must itself pass the success test."""
        params = make_params(
            current_age=60, retirement_age=60, horizon_age=85,
            start_taxable=1_000_000.0, start_taxable_basis=1_000_000.0,
            annual_spending=50_000.0)
        found = retirement.sustainable_spending(
            params, ZERO_RETURN, threshold=0.9, paths=20, seed=1, tolerance=2000)
        rate = retirement.success_rate(
            dict(params, annual_spending=found), ZERO_RETURN, paths=20, seed=1)
        assert rate >= 0.9
        assert found > 0

    def test_more_capital_supports_more_spending(self):
        base = make_params(
            current_age=65, retirement_age=65, horizon_age=85,
            start_taxable=1_000_000.0, start_taxable_basis=1_000_000.0)
        rich = dict(base, start_taxable=2_000_000.0, start_taxable_basis=2_000_000.0)
        a = retirement.sustainable_spending(base, ZERO_RETURN, paths=20, seed=1, tolerance=2000)
        b = retirement.sustainable_spending(rich, ZERO_RETURN, paths=20, seed=1, tolerance=2000)
        assert b > a


class TestChartData:
    def test_median_path_is_ordered_and_banded(self):
        params = make_params(
            current_age=60, retirement_age=65, horizon_age=75,
            start_taxable=1_000_000.0, start_taxable_basis=1_000_000.0,
            annual_savings=20_000.0, annual_spending=60_000.0)
        path = retirement.median_path(params, [0.05, -0.02, 0.08], paths=30, seed=1)
        assert path[0]["age"] == 60
        assert [p["age"] for p in path] == sorted(p["age"] for p in path)
        for point in path:
            assert point["p10"] <= point["p50"] <= point["p90"]


class TestAccountClassification:
    """The wrapper a balance sits in decides when it can be spent, so
    misclassifying an account silently changes the retirement date."""

    @pytest.mark.parametrize("account,expected", [
        ("BOSSA IKE", "ike"),
        ("BOSSA IKE-M", "ike"),        # maklerskie IKE — same tax treatment
        ("IKE Obligacje", "ike"),
        ("BOSSA IKZE", "ikze"),
        ("Interactive Brokers", "taxable"),
        ("XTB (PLN)", "taxable"),
        ("tastytrade", "taxable"),
        ("Obligacje Skarbowe", "taxable"),
        (None, "taxable"),
    ])
    def test_accounts_map_to_the_right_wrapper(self, account, expected):
        import app as app_module
        assert app_module._classify_account(account) == expected

    def test_ikze_is_not_swallowed_by_the_ike_match(self):
        """'IKZE' contains 'IKE' as a substring, so order of checks matters."""
        import app as app_module
        assert app_module._classify_account("BOSSA IKZE") == "ikze"


class TestPlannerApi:
    def test_reports_unavailable_without_snapshots(self, client):
        body = client.get("/api/retirement").get_json()
        assert body["available"] is False

    def test_returns_a_plan_and_splits_balances(self, client, make_snapshot):
        import db as db_module
        sid = db_module.create_snapshot("2026-Q1", "2026-03-31")
        db_module.insert_positions(sid, [
            {"name": "X", "ticker": "X", "isin": None, "account": "Interactive Brokers",
             "group_name": "g", "currency": "PLN", "tags": "t", "value_pln": 500_000.0},
            {"name": "Y", "ticker": "Y", "isin": None, "account": "BOSSA IKE",
             "group_name": "g", "currency": "PLN", "tags": "t", "value_pln": 300_000.0},
            {"name": "Z", "ticker": "Z", "isin": None, "account": "BOSSA IKZE",
             "group_name": "g", "currency": "PLN", "tags": "t", "value_pln": 100_000.0},
        ])
        body = client.get("/api/retirement").get_json()
        assert body["available"] is True
        assert body["balances"]["taxable"] == pytest.approx(500_000)
        assert body["balances"]["ike"] == pytest.approx(300_000)
        assert body["balances"]["ikze"] == pytest.approx(100_000)
        assert len(body["path"]) > 0

    def test_settings_round_trip(self, client, make_snapshot):
        make_snapshot("2026-Q1", "2026-03-31", portfolio=1_000_000.0)
        client.post("/api/retirement", json={"current_age": 47, "annual_spending": 99000})
        body = client.get("/api/retirement").get_json()
        assert float(body["settings"]["current_age"]) == 47
        assert float(body["settings"]["annual_spending"]) == 99000

    def test_unknown_keys_are_not_persisted(self, client, make_snapshot):
        make_snapshot("2026-Q1", "2026-03-31", portfolio=1_000_000.0)
        resp = client.post("/api/retirement", json={"current_age": 50, "evil_key": "x"})
        assert resp.get_json()["saved"] == ["current_age"]
        import db as db_module
        assert "evil_key" not in db_module.get_retirement_settings()

    def test_higher_spending_pushes_the_earliest_age_later(self, client, make_snapshot):
        make_snapshot("2026-Q1", "2026-03-31", portfolio=2_000_000.0)
        client.post("/api/retirement", json={
            "current_age": 45, "annual_spending": 60000, "annual_savings": 50000})
        cheap = client.get("/api/retirement").get_json()["earliest_feasible_age"]
        client.post("/api/retirement", json={"annual_spending": 400000})
        dear = client.get("/api/retirement").get_json()["earliest_feasible_age"]
        assert cheap is not None
        assert dear is None or dear > cheap
