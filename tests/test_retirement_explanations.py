"""Reconciled failure explanations, sample consistency and withdrawal taxes."""
import random

import pytest

import db
import retirement
from test_retirement import make_params


def test_bridge_failure_names_locked_accounts_and_gap():
    params=make_params(current_age=55,retirement_age=55,horizon_age=70,
                       annual_spending=100,start_taxable=25,start_taxable_basis=25,
                       start_ike=1000,start_ike_basis=1000,start_ikze=200,start_ppk=50)
    f=retirement.failure_analysis(params,[0],paths=7,seed=42)
    assert f['failed_count']==f['with_locked_capital']==7
    assert f['depleted']==0 and f['median_age']==55
    e=f['example']
    assert e['shortfall']==75 and e['funded_from_capital']==25
    assert e['reachable_net']==0 and e['locked']==1250
    assert {a['bucket']:a['access_age'] for a in e['locked_accounts']}=={'ike':60,'ikze':65,'ppk':60}
    assert e['spending']==e['income']+e['funded_from_capital']+e['shortfall']


def test_exhausted_capital_is_distinct_from_age_locked_capital():
    params=make_params(current_age=70,retirement_age=70,horizon_age=75,
                       annual_spending=100,start_ike=50,start_ike_basis=50)
    f=retirement.failure_analysis(params,[0],paths=3,seed=42)
    assert f['depleted']==3 and f['with_locked_capital']==0
    assert f['example']['locked_accounts']==[]
    assert f['example']['shortfall']==50


def test_taxable_final_withdrawal_keeps_gain_tax_and_explains_shortfall():
    params=make_params(current_age=70,retirement_age=70,horizon_age=71,
                       annual_spending=100,start_taxable=100,start_taxable_basis=50,
                       belka_rate=.2)
    ok,rows=retirement.simulate_path(params,[0],random.Random(0))
    assert not ok
    assert rows[0]['withdrawal_tax']==pytest.approx(10)
    assert rows[0]['funded_from_capital']==pytest.approx(90)
    assert rows[0]['shortfall']==pytest.approx(10)


def test_taxable_partial_withdrawal_and_remaining_net_balance():
    params=make_params(current_age=70,retirement_age=70,horizon_age=71,
                       annual_spending=45,start_taxable=100,start_taxable_basis=50,belka_rate=.2)
    ok,rows=retirement.simulate_path(params,[0],random.Random(0))
    assert ok
    assert rows[0]['taxable']==pytest.approx(50)
    assert rows[0]['withdrawal_tax']==pytest.approx(5)
    assert rows[0]['reachable_net']==pytest.approx(45)


def test_healthy_plan_and_guaranteed_income_have_no_failure_example():
    params=make_params(current_age=65,retirement_age=65,horizon_age=80,annual_spending=100,zus_annual=100)
    f=retirement.failure_analysis(params,[0],paths=5,seed=42)
    assert f['failed_count']==0 and f['success_rate']==1
    assert f['example'] is None and f['median_age'] is None


def test_failure_sample_agrees_with_success_and_chart():
    params=make_params(current_age=55,retirement_age=55,horizon_age=70,
                       annual_spending=100,start_taxable=1200,start_taxable_basis=1200)
    returns=[-.2,.02,.15]
    f=retirement.failure_analysis(params,returns,paths=31,seed=42)
    assert 0<f['failed_count']<31
    assert f==retirement.failure_analysis(params,returns,paths=31,seed=42)
    assert f['success_rate']==pytest.approx(retirement.success_rate(params,returns,paths=31,seed=42))
    path=retirement.median_path(params,returns,paths=31,seed=42)
    assert path[-1]['failed_share']==f['failed_count']/31


def test_locked_percentile_is_computed_from_locked_balances():
    params=make_params(current_age=55,retirement_age=55,horizon_age=66,
                       annual_spending=100,start_taxable=250,start_taxable_basis=250,
                       start_ike=1000,start_ike_basis=1000)
    returns=[-.3,.15]
    rng=random.Random(42)
    runs=[retirement.simulate_path(params,returns,random.Random(rng.getrandbits(64)),False)[1] for _ in range(11)]
    path=retirement.median_path(params,returns,paths=11,seed=42)
    for i,row in enumerate(path):
        assert row['locked_p50']==sorted(r[i]['locked'] for r in runs)[5]
    assert path[-1]['locked_p50']==0


def test_retirement_api_explanations_preserve_baseline_and_report_mortgage(client,make_snapshot):
    make_snapshot('2026-Q2','2026-06-30',portfolio=100,cash=25,mortgage=500,ppk=1000)
    original=db.get_retirement_settings()
    response=client.post('/api/retirement/preview',json=dict(current_age=55,retirement_age=55,
                         horizon_age=70,annual_spending=1000,use_historical_returns=0,expected_real_return=0))
    assert response.status_code==200
    data=response.get_json()
    f=data['failure_analysis']
    assert f['paths']==300 and f['failed_count']==300
    assert data['mortgage_balance']==500
    assert data['chosen_age_success_rate']==0
    assert data['path'][-1]['failed_share']==1
    assert db.get_retirement_settings()==original
    assert data['projection'][0]['withdrawal_tax']>=0


def test_missing_mortgage_is_not_reported_as_confirmed_zero(client,make_snapshot):
    make_snapshot('2026-Q2','2026-06-30',portfolio=100)
    data=client.post('/api/retirement/preview',json=dict(current_age=65,retirement_age=65,horizon_age=66)).get_json()
    assert data['mortgage_balance'] is None
